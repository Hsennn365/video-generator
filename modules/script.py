import csv
import json
import os
import re
import requests
from difflib import SequenceMatcher

from config import (
    CHANNEL_NAME,
    CONTENT_TONE,
    TARGET_AUDIENCE,
)

from modules.research import (
    ensure_series_research,
    load_series_research,
    get_research_hash,
)

try:
    from config import OPENAI_API_KEY
except ImportError:
    OPENAI_API_KEY = ""

try:
    from config import SCRIPT_MODEL
except ImportError:
    SCRIPT_MODEL = "gpt-4.1"

try:
    from config import TOPICS_FILE
except ImportError:
    TOPICS_FILE = "topics.csv"

try:
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "output"


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

TARGET_RUNTIME_SECONDS = 70
TARGET_SCENES = 10
TARGET_WORDS_LOW = 120
TARGET_WORDS_HIGH = 155

PERFORMANCE_MARKER_RE = re.compile(
    r"\[\[(pause|voice):([^\]]+)\]\]",
    re.IGNORECASE,
)

CTA_RE = re.compile(
    r"\bFollow for\s+[^.!?]+(?:[.!?]|$)",
    re.IGNORECASE,
)

PART_PREFIX_RE = re.compile(r"^\s*Part\s+\d+\.\s*", re.IGNORECASE)

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "for", "in", "on", "at",
    "by", "with", "from", "into", "over", "under", "as", "is", "was", "were", "be",
    "been", "being", "it", "its", "this", "that", "these", "those", "he", "she",
    "they", "them", "his", "her", "their", "we", "you", "your", "our", "not", "no",
    "did", "do", "does", "had", "has", "have", "will", "would", "could", "should",
    "part", "follow", "more", "dark", "history", "next", "story",
}

UNSAFE_PHRASES = [
    "rivers of blood",
    "bodies filled the streets",
    "massacre",
    "slaughter",
    "gore",
    "dismembered",
    "no one was spared",
]


# ---------------------------------------------------------------------------
# Research context formatter
# ---------------------------------------------------------------------------

def _format_research_context(research: dict | None) -> str:
    """
    Convert a research bible dict into a compact string for injection into user
    prompts. Sources are stored internally in the research file and must NOT
    appear as visible citations in any script output.
    Returns an empty string when research is None.
    """
    if not research:
        return ""

    lines: list[str] = [
        "=== VERIFIED RESEARCH CONTEXT (internal — do not cite in script output) ===",
    ]

    timeline = research.get("historical_timeline") or []
    if timeline:
        lines.append("\nHISTORICAL TIMELINE:")
        for entry in timeline:
            year = entry.get("year", "")
            event = entry.get("event", "")
            sig = entry.get("significance", "")
            lines.append(f"  {year}: {event}" + (f" — {sig}" if sig else ""))

    people = research.get("important_people") or []
    if people:
        lines.append("\nKEY PEOPLE:")
        for p in people:
            lines.append(
                f"  {p.get('name', '')}: {p.get('role', '')}. {p.get('key_actions', '')}"
            )

    places = research.get("important_places") or []
    if places:
        lines.append("\nKEY PLACES:")
        for pl in places:
            lines.append(f"  {pl.get('name', '')}: {pl.get('location_context', '')}")

    chain = research.get("cause_effect_chain") or []
    if chain:
        lines.append("\nCAUSE → EFFECT CHAIN:")
        for link in chain:
            gap = f" ({link.get('time_gap', '')})" if link.get("time_gap") else ""
            lines.append(f"  {link.get('cause', '')} → {link.get('effect', '')}{gap}")

    must_include = research.get("must_include_facts") or []
    if must_include:
        lines.append("\nFACTS THE SCRIPT MUST INCLUDE:")
        for fact in must_include:
            lines.append(f"  - {fact}")

    must_not = research.get("must_not_imply_facts") or []
    if must_not:
        lines.append("\nTHINGS THE SCRIPT MUST NOT IMPLY:")
        for fact in must_not:
            lines.append(f"  - {fact}")

    open_q = research.get("open_questions_or_uncertainties") or []
    if open_q:
        lines.append("\nHISTORICAL UNCERTAINTIES (treat with caution):")
        for q in open_q:
            lines.append(f"  - {q}")

    lines.append(
        "\nNOTE: Use this research to ground dates, people, places, and cause/effect naturally "
        "in the narration. Do NOT include academic citations, source names, or URLs in any "
        "script output. Sources are for internal grounding only."
    )
    lines.append("=" * 70)

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# OpenAI helpers
# -----------------------------------------------------------------------------

def _extract_response_text(payload: dict) -> str:
    """Extract text from an OpenAI Responses API payload."""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()

    pieces: list[str] = []
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    pieces.append(text)

    text = "\n".join(pieces).strip()
    if not text:
        raise Exception(f"OpenAI response had no readable text: {payload}")
    return text


def _is_billing_or_quota_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(
        phrase in text
        for phrase in [
            "billing_hard_limit_reached",
            "insufficient_quota",
            "quota",
            "429",
            "insufficient funds",
            "balance",
            "billing",
            "limit reached",
        ]
    )


def _openai_text_response(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_output_tokens: int,
    temperature: float | None = 0.65,
    reasoning_effort: str | None = None,
) -> str:
    """
    Calls OpenAI Responses API for text generation.
    Handles GPT-5 family models (no temperature allowed).
    """
    if not str(OPENAI_API_KEY or "").strip():
        raise Exception("Set OPENAI_API_KEY in config.py for GPT script generation.")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Detect GPT-5 family (GPT-5, GPT-5.5, etc.)
    is_gpt5 = "gpt-5" in model.lower() or "5.5" in model.lower()

    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
        "max_output_tokens": max_output_tokens,
    }

    if is_gpt5:
        # GPT-5 family models do not support temperature but support reasoning_effort
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
    else:
        if temperature is not None:
            payload["temperature"] = temperature

    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers=headers,
        json=payload,
        timeout=300,
    )

    if not is_gpt5 and response.status_code == 400 and "temperature" in response.text.lower():
        payload.pop("temperature", None)
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers=headers,
            json=payload,
            timeout=300,
        )

    if response.status_code != 200:
        err_msg = response.text[:2000]
        if _is_billing_or_quota_error(err_msg):
            print(f"  🛑 OpenAI API stop (billing/quota): {err_msg}")
            raise Exception(f"BILLING_OR_QUOTA_STOP: {err_msg}")
        raise Exception(
            f"OpenAI text error {response.status_code}: {err_msg}"
        )

    return _extract_response_text(response.json())


def _extract_json(raw: str, prefix: str = "script") -> dict:
    """
    Extract JSON even if GPT wraps it in fences and clean up common errors.
    Standardized to match research.py robustness.
    """
    text = raw.strip()
    original_raw = text

    # 1. Strip markdown fences
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        text = text.strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    # 2. Try direct load
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Extract from first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    # 4. Cleanup trailing commas and try again
    text = re.sub(r",\s*([}\]])", r"\1", text)
    
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # 5. Save to debug file and raise
        from config import TEMP_DIR
        from datetime import datetime
        os.makedirs(TEMP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_path = os.path.join(TEMP_DIR, f"invalid_{prefix}_json_{timestamp}.txt")
        
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(f"ERROR: {str(e)}\n\n")
            f.write("CLEANED CONTENT:\n")
            f.write(text)
            f.write("\n\nORIGINAL RAW RESPONSE:\n")
            f.write(original_raw)
        
        raise Exception(
            f"Failed to parse {prefix} JSON after cleanup. Debug file: {debug_path}. Error: {e}"
        ) from e


# -----------------------------------------------------------------------------
# Text cleanup / normalization helpers
# -----------------------------------------------------------------------------

def _clean_text_spacing(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _remove_performance_markers(text: str) -> str:
    return PERFORMANCE_MARKER_RE.sub("", str(text or "")).strip()


def _strip_markers_and_spaces(text: str) -> str:
    return _clean_text_spacing(_remove_performance_markers(text))


def _strip_orphan_leading_punctuation(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"^\s*[\.,!?;:]+(?:\s*[\.,!?;:]+)*\s*", "", text)
    return _clean_text_spacing(text)


def _clean_sentence_end(text: str) -> str:
    text = _clean_text_spacing(text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    text = re.sub(r"([.!?]){2,}", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _remove_existing_cta(text: str, cta: str | None = None) -> str:
    raw = str(text or "")
    if cta:
        raw = re.sub(re.escape(cta), "", raw, flags=re.IGNORECASE)
    raw = CTA_RE.sub("", raw)
    raw = _strip_orphan_leading_punctuation(raw)
    raw = _clean_sentence_end(raw)
    return raw.strip()


def _is_empty_or_punctuation_only(text: str) -> bool:
    cleaned = _strip_markers_and_spaces(text)
    cleaned = _remove_existing_cta(cleaned)
    return not re.sub(r"[^a-zA-Z0-9]+", "", cleaned)


def _is_cta_only(text: str, cta: str | None = None) -> bool:
    cleaned = _remove_existing_cta(_strip_markers_and_spaces(text), cta=cta)
    return _is_empty_or_punctuation_only(cleaned)


def _is_part_label_only(text: str, part_num: int) -> bool:
    alnum = re.sub(r"[^a-zA-Z0-9]+", "", _strip_markers_and_spaces(text)).lower()
    return bool(part_num) and alnum == f"part{part_num}"


def _first_sentence(text: str, fallback_words: int = 18) -> str:
    text = _strip_markers_and_spaces(text)
    if not text:
        return ""

    match = re.search(r"^(.+?[.!?])(?:\s|$)", text)
    if match:
        return match.group(1).strip()

    words = text.split()
    return " ".join(words[:fallback_words]).strip()


def _sentences(text: str) -> list[str]:
    text = _strip_markers_and_spaces(text)
    if not text:
        return []
    parts = re.findall(r"[^.!?]+[.!?]?", text)
    return [_clean_sentence_end(p) for p in parts if _clean_sentence_end(p)]


def _meaningful_hook_from_scene1(scene1_segment: str, is_series: bool, part_num: int) -> str:
    seg = _clean_text_spacing(_strip_markers_and_spaces(scene1_segment))
    if not seg:
        return f"Part {part_num}." if is_series and part_num > 0 else ""

    if not is_series:
        return _first_sentence(seg)

    prefix = f"Part {part_num}."
    rest = re.sub(rf"^\s*Part\s+{part_num}\.\s*", "", seg, flags=re.IGNORECASE).strip()
    rest_sentences = _sentences(rest)

    if not rest_sentences:
        return prefix

    chosen: list[str] = []
    meaningful_word_count = 0
    for sentence in rest_sentences:
        chosen.append(sentence)
        meaningful_word_count += len(_normal_tokens(sentence))
        if meaningful_word_count >= 4 or len(chosen) >= 2:
            break

    return _clean_text_spacing(f"{prefix} {' '.join(chosen)}")


def _normal_tokens(text: str) -> list[str]:
    text = _remove_existing_cta(_strip_markers_and_spaces(text))
    text = PART_PREFIX_RE.sub("", text)
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in STOPWORDS]


def _normalized_segment(text: str) -> str:
    return " ".join(_normal_tokens(text))


def _similarity(a: str, b: str) -> float:
    na = _normalized_segment(a)
    nb = _normalized_segment(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _last_meaningful_sentence(text: str) -> str:
    cleaned = _remove_existing_cta(text)
    sentences = _sentences(cleaned)
    return sentences[-1] if sentences else cleaned


def _count_cta_occurrences_in_scenes(script: dict, cta: str) -> int:
    cta_plain = str(cta or "").lower().strip()
    if not cta_plain:
        return 0

    count = 0
    for scene in script.get("scenes", []) or []:
        seg = str(scene.get("narration_segment", "") or "").lower()
        count += seg.count(cta_plain)
    return count


def _clean_script_markers(script: dict) -> dict:
    """Markers are allowed in full_narration only, never scene fields."""
    script["title"] = _remove_performance_markers(script.get("title", ""))
    script["hook"] = _remove_performance_markers(script.get("hook", ""))
    script["cta"] = _remove_performance_markers(script.get("cta", ""))
    script["youtube_description"] = _remove_performance_markers(
        script.get("youtube_description", "")
    )

    for scene in script.get("scenes", []) or []:
        scene["narration_segment"] = _remove_performance_markers(
            scene.get("narration_segment", "")
        )
        scene["scene_description"] = _remove_performance_markers(
            scene.get("scene_description", "")
        )

    return script


# -----------------------------------------------------------------------------
# Series helpers
# -----------------------------------------------------------------------------

def _get_episode_num(topic: dict) -> int:
    try:
        return int(topic.get("episode_number") or topic.get("part") or 0)
    except ValueError:
        return 0

def _is_series_topic(topic: dict) -> bool:
    # Fallback to check "series" if "type" isn't present in old rows
    if topic.get("type") == "series":
        return True
    if topic.get("type") == "single":
        return False
    # Legacy fallback
    return bool(topic.get("series")) and str(topic.get("part", "0")) != "0"


def _series_total_parts(topic: dict) -> int | None:
    """
    Return the number of episodes for this series.
    Prioritizes the resolved_episode_count from the series_package.json if available.
    """
    # Try loading from series_package first
    package_path = get_series_package_path(topic)
    if os.path.isfile(package_path):
        try:
            with open(package_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                resolved = data.get("resolved_episode_count")
                if resolved:
                    return int(resolved)
        except Exception:
            pass

    # Fallback to CSV
    try:
        val = str(topic.get("episode_count", "")).strip().lower()
        if val and val != "auto":
            return int(val)
    except ValueError:
        pass
    
    return None


def get_episode_count_mode(topic: dict) -> str:
    """Return 'fixed' if episode_count is a number, else 'dynamic'."""
    val = str(topic.get("episode_count", "")).strip().lower()
    if not val or val == "auto":
        return "dynamic"
    try:
        int(val)
        return "fixed"
    except ValueError:
        return "dynamic"


def get_min_episodes(topic: dict) -> int:
    """Return min_episodes from topic or default 3."""
    try:
        val = topic.get("min_episodes")
        if val and str(val).strip():
            return int(val)
    except ValueError:
        pass
    return 3


def get_max_episodes(topic: dict) -> int:
    """Return max_episodes from topic or default 10."""
    try:
        val = topic.get("max_episodes")
        if val and str(val).strip():
            return int(val)
    except ValueError:
        pass
    return 10


def _series_cta(topic: dict) -> str:
    part = _get_episode_num(topic)
    total = _series_total_parts(topic)
    if total and part >= total:
        return "Follow for the next dark history story."
    return f"Follow for part {part + 1}."


def _topic_part(topic: dict) -> int:
    return _get_episode_num(topic)


def resolve_episode_count(topic: dict, research_context: str) -> dict:
    """
    Determine the optimal number of episodes for a series using LLM.
    Returns a dict with resolved_episode_count and series_scope_plan.
    """
    mode = get_episode_count_mode(topic)
    min_eps = get_min_episodes(topic)
    max_eps = get_max_episodes(topic)
    
    if mode == "fixed":
        fixed_count = _series_total_parts(topic) or 10
        return {
            "resolved_episode_count": fixed_count,
            "series_scope_plan": {
                "recommended_episode_count": fixed_count,
                "reason": "Fixed count provided in CSV.",
                "too_short_risk": "N/A",
                "too_long_risk": "N/A",
                "audience_fit": "N/A",
                "episode_count_bounds": {"min": min_eps, "max": max_eps}
            }
        }

    print(f"   🎯 Mode is dynamic. Asking AI to resolve optimal episode count (bounds: {min_eps}-{max_eps})...")

    system_prompt = f"""You are a series producer for a high-tension history channel on TikTok and YouTube Shorts.
Your goal is to determine the optimal number of episodes for a new series based on the research provided.

CRITICAL RULES:
1. Do NOT maximize the number of episodes. 
2. Choose the SMALLEST number of episodes that covers the important story without skipping key events.
3. Every episode must be exciting and avoid documentary filler.
4. If an episode would have fewer than 3 meaningful story beats, it should be merged.
5. If an episode feels like background/context only, it should be merged or rewritten with conflict.

The audience has a short attention span. Every part must have a reason to exist.

Return ONLY this JSON structure:
{{
  "recommended_episode_count": 7,
  "reason": "Why this number fits the story exactly",
  "too_short_risk": "What critical story would be lost if we had fewer episodes",
  "too_long_risk": "What specific part would become boring filler if we had more episodes",
  "audience_fit": "Why this pacing works for short-form video",
  "episode_count_bounds": {{
    "min": {min_eps},
    "max": {max_eps}
  }}
}}
"""

    user_prompt = f"""SERIES TITLE: {topic.get('title')}
DESCRIPTION: {topic.get('description')}

{research_context}

Determine the recommended_episode_count. 
Constraints: min={min_eps}, max={max_eps}.
"""

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=1800,
        temperature=0.4,
        reasoning_effort="medium",
    )

    try:
        plan = _extract_json(raw, prefix="series_scope")
    except Exception as e:
        print(f"⚠️  Series scope JSON is malformed. Attempting one-time repair... (Error: {e})")
        repair_system = (
            "You are a JSON repair specialist. Repair the provided malformed JSON "
            "into strictly valid JSON matching the requested schema. "
            "Return ONLY the valid JSON."
        )
        repair_user = f"Malformed JSON to fix:\n\n{raw}"
        try:
            repaired_raw = _openai_text_response(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=SCRIPT_MODEL,
                max_output_tokens=1800,
                reasoning_effort="low"
            )
            plan = _extract_json(repaired_raw, prefix="series_scope_repaired")
            print("✅ Series scope JSON repaired successfully.")
        except Exception as e2:
            raise Exception(f"Failed to generate or repair series scope JSON: {e}") from e2
    rec_count = plan.get("recommended_episode_count")
    
    if not isinstance(rec_count, int):
        try:
            rec_count = int(rec_count)
        except (ValueError, TypeError):
            rec_count = min_eps

    # Clamp
    final_count = max(min_eps, min(max_eps, rec_count))
    if final_count != rec_count:
        print(f"   ⚠️ AI recommended {rec_count} episodes, but clamping to bounds: {final_count}")
    
    print(f"   ✅ AI resolved series to {final_count} episodes.")
    
    return {
        "resolved_episode_count": final_count,
        "series_scope_plan": plan
    }


def _next_episode_topic(topic: dict) -> dict | None:
    # This usually requires topics.csv access. For now return None to avoid crash.
    # The pipeline will use fallback strings.
    return None


def _get_previous_episode_script(topic: dict) -> dict | None:
    part = _get_episode_num(topic)
    if part <= 1:
        return None
    
    topic_id = str(topic.get("id", "0")).strip()
    
    # 1. New series-first topics
    if topic.get("type") == "series":
        try:
            prev_path = get_script_path(topic, episode_num=part - 1)
            return _load_json_file(prev_path, default=None)
        except Exception:
            return None
            
    # 2. Legacy series topics
    if _is_series_topic(topic):
        try:
            prev_id = str(int(topic_id) - 1)
            prev_path = os.path.join(OUTPUT_DIR, f"topic_{prev_id}_script.json")
            return _load_json_file(prev_path, default=None)
        except (ValueError, TypeError):
            pass
            
    return None


def get_asset_prefix(topic: dict, episode_num: int | None = None) -> str:
    topic_id = str(topic.get("id", "0")).strip()
    if _is_series_topic(topic):
        ep = int(episode_num) if episode_num else 1
        return f"series_{topic_id}_episode_{ep:02d}_"
    return f"topic_{topic_id}_"


def get_script_path(topic: dict, episode_num: int | None = None) -> str:
    topic_id = str(topic.get("id", "0")).strip()
    
    try:
        from config import SERIES_DATA_DIR
    except ImportError:
        SERIES_DATA_DIR = "series_data"
        
    try:
        from config import OUTPUT_DIR
    except ImportError:
        OUTPUT_DIR = "output"

    if _is_series_topic(topic):
        if episode_num is None:
            raise ValueError(f"episode_num is required for series topics. Topic ID: {topic_id}")
        ep = int(episode_num)
        return os.path.join(SERIES_DATA_DIR, f"series_{topic_id}", "episodes", f"episode_{ep:02d}_script.json")
    
    return os.path.join(OUTPUT_DIR, f"topic_{topic_id}_script.json")


def get_series_package_path(topic: dict) -> str:
    topic_id = str(topic.get("id", "0")).strip()
    from config import SERIES_DATA_DIR
    return os.path.join(SERIES_DATA_DIR, f"series_{topic_id}", "series_package.json")


def _load_json_file(path: str, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _compact_script_for_context(script: dict) -> dict:
    scenes = script.get("scenes", []) or []
    return {
        "topic_id": script.get("topic_id"),
        "part": script.get("part"),
        "title": script.get("title"),
        "hook": script.get("hook"),
        "full_narration": script.get("full_narration"),
        "opening_scenes": [
            scene.get("narration_segment", "") for scene in scenes[:3]
        ],
        "final_scenes": [
            scene.get("narration_segment", "") for scene in scenes[-3:]
        ],
        "final_scene": scenes[-1].get("narration_segment", "") if scenes else "",
        "cta": script.get("cta"),
    }


# -----------------------------------------------------------------------------
# Script post-processing
# -----------------------------------------------------------------------------

def _validate_script_shape(script: dict) -> dict:
    required_top_fields = {
        "title": "Untitled",
        "hook": "",
        "full_narration": "",
        "scenes": [],
        "hashtags": [],
        "youtube_description": "",
        "cta": "Follow for more dark history.",
    }

    for key, fallback in required_top_fields.items():
        if key not in script:
            script[key] = fallback

    if not isinstance(script.get("scenes"), list):
        script["scenes"] = []

    for i, scene in enumerate(script["scenes"], start=1):
        scene.setdefault("scene_number", i)
        scene.setdefault("timestamp_start", 0)
        scene.setdefault("timestamp_end", 0)
        scene.setdefault("narration_segment", "")
        scene.setdefault("image_prompt", "")
        scene.setdefault("scene_description", "")

    return script


def _ensure_series_opening(script: dict, topic: dict) -> dict:
    """Make Scene 1 the real spoken hook."""
    scenes = script.get("scenes", []) or []
    if not scenes:
        return script

    first = scenes[0]
    seg = _clean_text_spacing(_strip_markers_and_spaces(first.get("narration_segment", "")))
    declared_hook = _clean_text_spacing(_strip_markers_and_spaces(script.get("hook", "")))

    is_series = _is_series_topic(topic)
    part_num = _get_episode_num(topic)
    prefix = f"Part {part_num}." if is_series else ""

    if is_series and not seg.lower().startswith(prefix.lower()):
        seg = _clean_text_spacing(f"{prefix} {seg}")

    if is_series and _is_part_label_only(declared_hook, part_num):
        declared_hook = ""

    if declared_hook and declared_hook.lower() not in seg.lower():
        if is_series and seg.lower().startswith(prefix.lower()):
            rest = seg[len(prefix):].strip()
            seg = _clean_text_spacing(f"{prefix} {declared_hook} {rest}")
        else:
            seg = _clean_text_spacing(f"{declared_hook} {seg}")

    first["narration_segment"] = _clean_sentence_end(seg)
    script["hook"] = _meaningful_hook_from_scene1(first["narration_segment"], is_series, part_num)
    return script


def _fallback_final_line(topic: dict, previous_line: str = "") -> str:
    next_topic = _next_episode_topic(topic)
    previous_line = _last_meaningful_sentence(previous_line)
    if previous_line and not _is_empty_or_punctuation_only(previous_line):
        return previous_line

    if next_topic:
        next_title = str(next_topic.get("title", "")).strip()
        if next_title:
            return f"The next chapter was already moving: {next_title}."

    return "The next shadow was already moving."


def _ensure_series_cta(script: dict, topic: dict) -> dict:
    """Ensure CTA appears exactly once and only in the final scene."""
    scenes = script.get("scenes", []) or []
    if not scenes:
        return script

    cta = _series_cta(topic) if _is_series_topic(topic) else "Follow for more dark history."
    script["cta"] = cta

    previous_meaningful = ""
    for i, scene in enumerate(scenes):
        seg = _clean_text_spacing(scene.get("narration_segment", ""))
        cleaned = _remove_existing_cta(seg, cta=cta)

        if i < len(scenes) - 1:
            if not _is_empty_or_punctuation_only(cleaned):
                scene["narration_segment"] = _clean_sentence_end(cleaned)
                previous_meaningful = scene["narration_segment"]
            else:
                bridge = previous_meaningful or "The silence before the next blow grew heavier."
                scene["narration_segment"] = bridge
                previous_meaningful = bridge
            continue

        if _is_empty_or_punctuation_only(cleaned):
            final_line = _fallback_final_line(topic, previous_meaningful)
        else:
            final_line = _clean_sentence_end(cleaned)

        final_line = _remove_existing_cta(final_line, cta=cta)
        final_line = final_line.rstrip(" .,;:!?")
        if not final_line:
            final_line = _fallback_final_line(topic, previous_meaningful).rstrip(" .,;:!?")

        scene["narration_segment"] = f"{final_line}. {cta}".strip()

    return script


def _scene_summary_for_prompt(narration_segment: str, scene_description: str = "") -> str:
    source = _first_sentence(narration_segment) or _first_sentence(scene_description)
    if not source:
        return "a quiet dark historical moment"
    source = _remove_existing_cta(source)
    source = PART_PREFIX_RE.sub("", source).strip(" .,!?:;")
    return source[:180] or "a quiet dark historical moment"


def _ensure_scene_visual_assets(script: dict) -> dict:
    for scene in script.get("scenes", []) or []:
        seg = scene.get("narration_segment", "")
        desc = scene.get("scene_description", "")
        summary = _scene_summary_for_prompt(seg, desc)

        if not str(scene.get("image_prompt", "") or "").strip():
            scene["image_prompt"] = (
                "Premium dark historical graphic novel panel showing "
                f"{summary}, ancient Mediterranean setting, dramatic shadows, "
                "vertical 9:16 composition. Must show: the main subject of the scene. "
                "Must not show: text, watermark, modern objects, gore, blood, nudity."
            )

        if not str(scene.get("scene_description", "") or "").strip():
            scene["scene_description"] = _first_sentence(seg) or "A dark historical moment."

    return script


def _normalize_scene_timestamps(script: dict, topic: dict) -> dict:
    scenes = script.get("scenes", []) or []
    if not scenes:
        return script

    scene_count = len(scenes)
    is_series = _is_series_topic(topic)
    if is_series:
        runtime = TARGET_RUNTIME_SECONDS
    else:
        # Scale standalone runtime based on scene count: 6.5s per scene
        # Clamp between 55.0 and 70.0 seconds
        runtime = max(55.0, min(70.0, scene_count * 6.5))

    seconds_per_scene = runtime / max(scene_count, 1)

    for i, scene in enumerate(scenes, start=1):
        scene["scene_number"] = i
        scene["timestamp_start"] = round((i - 1) * seconds_per_scene, 2)
        scene["timestamp_end"] = round(i * seconds_per_scene, 2)

    scenes[-1]["timestamp_end"] = runtime
    return script


def _insert_marker_between_scenes(index_before_next: int, next_text: str) -> str:
    """Return a performance marker to place before the next scene."""
    # index_before_next is the zero-based scene index being appended.
    if index_before_next == 2:
        return "[[pause:0.4]]"
    if index_before_next == 4:
        return "[[voice:tense]]"
    if index_before_next == 6:
        return "[[pause:0.6]]"
    if index_before_next == 9:
        return "[[voice:low]]"
    return ""


def _rebuild_full_narration_from_scenes(script: dict, topic: dict) -> dict:
    """Build full_narration only from final scene narration segments."""
    scenes = script.get("scenes", []) or []
    if not scenes:
        script["full_narration"] = ""
        return script

    cta = script.get("cta") or _series_cta(topic)
    parts: list[str] = []

    for i, scene in enumerate(scenes):
        seg = _clean_text_spacing(scene.get("narration_segment", ""))
        if not seg:
            continue

        if i == len(scenes) - 1 and cta and cta.lower() in seg.lower():
            before = _remove_existing_cta(seg, cta=cta)
            if before:
                before = before.rstrip(" .,;:!?")
                seg = f"{before}. [[pause:0.8]] {cta}"
            else:
                seg = cta

        if parts:
            marker = _insert_marker_between_scenes(i, seg)
            if marker:
                parts.append(marker)

        parts.append(seg)

    script["full_narration"] = _clean_text_spacing(" ".join(parts))
    return script


def _validate_exact_incident_bridge(script: dict, topic: dict, previous_script: dict | None) -> list[str]:
    issues: list[str] = []
    if not previous_script:
        return issues

    # Determine part number
    try:
        part = int(topic.get("part") or topic.get("episode_number") or script.get("part") or 0)
    except (ValueError, TypeError):
        part = 0

    prev_scenes = previous_script.get("scenes", []) or []
    # Look at the very end of the previous episode
    prev_text = " ".join(scene.get("narration_segment", "") for scene in prev_scenes[-3:])
    prev_text_lower = prev_text.lower()

    # Look at the beginning of the current episode
    first_four = " ".join(
        scene.get("narration_segment", "")
        for scene in (script.get("scenes", []) or [])[:4]
    ).lower()

    # Determine if this episode is meant to cover the Hannibal/Rome gates beat
    plan = topic.get("episode_plan", {})
    assigned_ids = plan.get("assigned_timeline_event_ids") or plan.get("timeline_event_ids") or []
    
    # We check if the plan or assigned events mention Hannibal NOT taking Rome
    is_hannibal_rome_beat = False
    
    # 1. Check plan fields
    plan_text = json.dumps(plan).lower()
    if "hannibal" in plan_text and "rome" in plan_text:
        if "never took" in plan_text or "did not take" in plan_text or "siege" in plan_text or "gates" in plan_text:
            is_hannibal_rome_beat = True
            
    # 2. Check previous episode cliffhanger (legacy support / backup)
    is_hannibal_cliffhanger = (
        "hannibal" in prev_text_lower
        and "rome" in prev_text_lower
        and (
            "gates" in prev_text_lower
            or "never took" in prev_text_lower
            or "stood" in prev_text_lower
        )
    )

    if is_hannibal_rome_beat or is_hannibal_cliffhanger:
        concepts = [
            "wall", "walls", "siege machine", "siege machines", "supplies",
            "supply", "exhausted", "tired", "no help", "sent no help",
            "refused to surrender", "could not storm", "africa",
            "called home", "forced home", "rome was too strong",
            "reinforcements", "numidian", "gates", "mercenary", "mercenaries"
        ]
        hits = sum(1 for concept in concepts if concept in first_four)
        
        # If we have a plan, we can also check anchors
        anchors = " ".join(plan.get("factual_anchors_to_include", [])).lower()
        if any(c in anchors for c in concepts):
            # The plan specifically asks for these concepts. 
            # If the script misses them, it's a failure.
            if hits < 1:
                issues.append(
                    f"Episode {part} must explain why Hannibal did not take Rome as required by the plan (missing siege/walls/reinforcements context)."
                )
        elif is_hannibal_cliffhanger and hits < 1:
            # If no plan, use lightweight check
            issues.append(
                f"Episode {part} should explain why Hannibal did not take Rome after the previous cliffhanger."
            )

    return issues


def _validate_script_quality_strict(
    script: dict,
    topic: dict,
    previous_script: dict | None = None,
    next_topic: dict | None = None,
) -> list[str]:
    issues: list[str] = []
    scenes = script.get("scenes", []) or []
    cta = script.get("cta") or _series_cta(topic)
    part = _get_episode_num(topic)

    is_series = _is_series_topic(topic)
    if is_series:
        if len(scenes) != TARGET_SCENES:
            issues.append(f"Expected {TARGET_SCENES} scenes for series episode, got {len(scenes)}")
    else:
        if len(scenes) < 8 or len(scenes) > TARGET_SCENES:
            issues.append(f"Expected between 8 and {TARGET_SCENES} scenes for standalone video, got {len(scenes)}")

    hook = script.get("hook", "")
    if _is_series_topic(topic) and _is_part_label_only(hook, part):
        issues.append("Hook is only the part label")

    if scenes:
        expected_prefix = f"Part {part}."
        if _is_series_topic(topic) and not scenes[0].get("narration_segment", "").lower().startswith(expected_prefix.lower()):
            issues.append(f"Scene 1 does not start with {expected_prefix}")

        final_seg = scenes[-1].get("narration_segment", "")
        if re.match(r"^\s*[\.,!?;:]", final_seg):
            issues.append("Final scene starts with orphan punctuation")
        if _is_cta_only(final_seg, cta=cta):
            issues.append("Final scene is CTA-only")

    for i, scene in enumerate(scenes, start=1):
        seg = scene.get("narration_segment", "")
        if _is_empty_or_punctuation_only(seg):
            issues.append(f"Scene {i} narration is empty or punctuation-only")
        if not str(scene.get("image_prompt", "") or "").strip():
            issues.append(f"Scene {i} has empty image_prompt")
        if i < len(scenes) and CTA_RE.search(seg):
            issues.append(f"Non-final scene {i} contains CTA")

        prompt_for_scan = str(scene.get("image_prompt", "") or "")
        # Do not scan negative safety constraints like:
        # "Must not show: text, watermark, modern objects, gore, blood, nudity."
        # Those words are intentionally present to PREVENT the image model from showing them.
        prompt_for_scan = re.sub(
            r"Must not show:.*$",
            "",
            prompt_for_scan,
            flags=re.IGNORECASE | re.DOTALL,
        )

        combined = f"{seg} {prompt_for_scan}".lower()
        for phrase in UNSAFE_PHRASES:
            if phrase in combined:
                issues.append(f"Scene {i} contains unsafe/over-graphic phrase: {phrase}")

    count = _count_cta_occurrences_in_scenes(script, cta)
    if count != 1:
        issues.append(f"CTA occurrence count should be 1, got {count}")

    scene_joined = _clean_text_spacing(" ".join(
        _clean_text_spacing(scene.get("narration_segment", ""))
        for scene in scenes
    ))
    narration_plain = _strip_markers_and_spaces(script.get("full_narration", ""))
    if scene_joined and scene_joined.lower() != narration_plain.lower():
        issues.append("full_narration does not exactly match scenes after marker removal")

    # Duplicate / near duplicate checks.
    normalized_counts: dict[str, int] = {}
    for scene in scenes:
        norm = _normalized_segment(scene.get("narration_segment", ""))
        if norm:
            normalized_counts[norm] = normalized_counts.get(norm, 0) + 1

    for norm, count_norm in normalized_counts.items():
        if count_norm >= 2 and len(norm.split()) >= 3:
            issues.append(f"Duplicate narration segment detected: {norm}")

    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            sim = _similarity(
                scenes[i].get("narration_segment", ""),
                scenes[j].get("narration_segment", ""),
            )
            if sim >= 0.75 and len(_normal_tokens(scenes[i].get("narration_segment", ""))) >= 3:
                issues.append(f"Near-duplicate scenes {i + 1} and {j + 1}")

    if len(scenes) >= 2:
        sim_final = _similarity(
            _remove_existing_cta(scenes[-2].get("narration_segment", ""), cta=cta),
            _remove_existing_cta(scenes[-1].get("narration_segment", ""), cta=cta),
        )
        if sim_final >= 0.70:
            issues.append(f"Scene {len(scenes)-1} and {len(scenes)} repeat the same final bridge line")

    # If 3+ scenes share the same dominant 3-token phrase, flag it.
    phrase_counts: dict[str, int] = {}
    for scene in scenes:
        tokens = _normal_tokens(scene.get("narration_segment", ""))
        if len(tokens) >= 3:
            phrase = " ".join(tokens[:3])
            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
    for phrase, count_phrase in phrase_counts.items():
        if count_phrase >= 3:
            issues.append(f"Too many scenes share same main phrase/action: {phrase}")

    issues.extend(_validate_exact_incident_bridge(script, topic, previous_script))

    return sorted(set(issues))


def _validate_script_quality(script: dict, topic: dict) -> None:
    issues = _validate_script_quality_strict(
        script,
        topic,
        previous_script=_get_previous_episode_script(topic),
        next_topic=_next_episode_topic(topic),
    )
    for issue in issues:
        print(f"   ⚠️ {issue}")


def _postprocess_script(script: dict, topic: dict) -> dict:
    script = _validate_script_shape(script)
    script = _clean_script_markers(script)
    script = _ensure_series_opening(script, topic)
    script = _ensure_series_cta(script, topic)
    script = _ensure_scene_visual_assets(script)
    script = _rebuild_full_narration_from_scenes(script, topic)
    script = _normalize_scene_timestamps(script, topic)

    script["title"] = _clean_text_spacing(script.get("title", ""))[:80]
    scenes = script.get("scenes", []) or []
    if scenes:
        script["hook"] = _meaningful_hook_from_scene1(
            scenes[0].get("narration_segment", ""),
            _is_series_topic(topic),
            _get_episode_num(topic),
        )
    else:
        script["hook"] = _clean_text_spacing(script.get("hook", ""))

    script["cta"] = _clean_text_spacing(script.get("cta", ""))
    script["youtube_description"] = _clean_text_spacing(
        script.get("youtube_description", "")
    )

    _validate_script_quality(script, topic)
    return script


# -----------------------------------------------------------------------------
# Prompt builders
# -----------------------------------------------------------------------------

def _series_context_for_single_topic(topic: dict) -> str:
    if not _is_series_topic(topic):
        return ""

    part_num = _get_episode_num(topic)
    series_name = str(topic.get("series", "") or "").strip()
    cta = _series_cta(topic)
    total_parts = _series_total_parts(series_name)

    total_text = (
        f"This series has {total_parts} known parts in topics.csv."
        if total_parts is not None
        else "The total number of parts is unknown, so assume there is a next part."
    )

    return f"""
This topic belongs to a series.

Series name:
{series_name}

Current part:
Part {part_num}

{total_text}

SERIES OPENING RULE:
Scene 1 narration_segment must begin naturally with:
"Part {part_num}."

SERIES ENDING RULE:
The final narration_segment must end with:
"{cta}"

The cta field must be exactly:
"{cta}"
"""


def _base_system_prompt(is_series: bool = True) -> str:
    if is_series:
        scene_count_rule = f"Use exactly {TARGET_SCENES} scenes."
    else:
        scene_count_rule = f"Use exactly {TARGET_SCENES} scenes (unless the script is very short and clearly needs fewer, e.g. 8–9 scenes). Do NOT stretch the story with filler."

    standalone_flow_instruction = ""
    if not is_series:
        standalone_flow_instruction = """
STANDALONE SCRIPT STRUCTURE & FLOW:
Your script must follow this exact narrative flow across the scenes (exactly 10 scenes by default):
1. Hook (Scene 1): Clear event/conflict stated in simple, direct words. No vague poetry.
2. Context (Scene 2-3): Establish the setting: where, when, and who.
3. Event (Scene 4-5): Detail exactly what happened.
4. Tension (Scene 6-7): Explain why leaders feared it or why this event mattered.
5. Consequence (Scene 8-9): What happened after this event.
6. Ending (Scene 10): Strong final cinematic line + exactly ONE follow CTA ("Follow for more dark history.").
"""

    return f"""
You are the lead writer and showrunner for {CHANNEL_NAME}, a short-form cinematic history channel.

You write dark, suspenseful history scripts for TikTok, Reels, and YouTube Shorts.
You are also writing for GPT Image, so every scene must be visually clear enough for an image model to understand.

The result must feel like:
- a movie trailer
- a dark documentary opening
- a story people cannot scroll past
- a premium graphic-novel history short

It must NOT feel like:
- a school lesson
- a Wikipedia summary
- a list of facts
- a generic history video

AUDIENCE:
{TARGET_AUDIENCE}

CHANNEL TONE:
{CONTENT_TONE}

CORE WRITING STYLE:
- Simple English
- Short sentences
- Cinematic rhythm
- Dark tension: Keep the tone dark, serious, and cinematic, but NEVER confusing. Use punchy, clear lines, not abstract, vague poetry.
- Clear cause and effect: explain what happened, who did it, why it mattered, and what the consequence was.
- Story first, facts second
- One visual idea at a time
- Emotional stakes
- Hook: The first line (Scene 1 narration_segment) must clearly state the core event/conflict in simple, direct words. Absolutely avoid vague or poetic hooks.
- Strong final line
- Every dramatic line must still tell the viewer a concrete fact. Do NOT write lines that only sound dramatic but contain zero actual information.
- Exactly one final CTA: Put exactly ONE follow CTA at the very end of the final scene's narration_segment ("Follow for more dark history."). Do NOT include any other CTA or duplicate CTA in any scene.

HOOK STYLE EXAMPLES:
- BAD Hook: "The night the war forgot itself."
- GOOD Hook: "In 1914, enemy soldiers climbed out of the trenches and stopped fighting."
- If writing about the Christmas Truce (or as a style example), the hook should directly explain:
  1. Soldiers from opposing sides stopped fighting.
  2. They met in no man’s land.
  3. Commanders/generals feared it because it broke military discipline.

DO NOT USE POETIC VAGUE PHRASES:
Avoid abstract, vague, or overly dramatic phrases that don't explain a concrete action or fact, such as:
- "the machine trembled"
- "silence swallowed the battlefield"
- "history held its breath"
- "the war forgot its name"

UNDERSTANDABILITY & CAPTIONS:
- Do NOT assume the viewer has prior historical knowledge of the event or specialized terms.
- Explain specialized terms simply in a single sentence if needed (e.g., explain what "no man's land" or "trenches" are, or why officers feared soldiers making friends/fraternizing with the enemy).
- The captions/narration must be 100% understandable to a general audience without prior context.

{standalone_flow_instruction}

TONE SAFETY:
- Keep the tone dark and cinematic, never graphic.
- Do NOT use phrases like: "rivers of blood", "bodies filled the streets", "massacre", "no one was spared", "slaughter", "gore", "dismembered".
- Prefer cinematic phrasings like: "the streets became silent", "the city broke under the assault", "smoke swallowed the ruins", "survivors vanished into the shadows".
- Image prompts must avoid blood, gore, dismemberment, explicit injury, and nudity.
- Image prompt "Must not show:" should include: text, watermark, modern objects, gore, blood, nudity.

SCENE RULES:
- {scene_count_rule}
- Each scene must be one clear visual beat.
- Each narration_segment should usually be 8–16 spoken words.
- Avoid micro-scenes.
- Avoid 1–4 word narration segments unless absolutely necessary.
- No rapid-fire image cuts.
- Each scene should be able to stay on screen for at least 3–5 seconds.
- No duplicate narration segments.
- No repeated scene beats.
- Each scene must advance the story.
- The final 4 scenes must be distinct.
- Do not use fallback repetition to fill scenes.

CRITICAL NARRATION SYNC RULES:
- full_narration must be made only from narration_segment values in order.
- Do not add a separate hook sentence outside the scenes.
- Scene 1 must contain the full opening hook.
- The final CTA must appear once only, inside the final scene.
- No scene can be CTA-only.
- The final scene must contain a dramatic closing line plus the CTA.

VOICE PERFORMANCE MARKERS:
You may use these markers inside full_narration only:
[[pause:0.4]]
[[pause:0.6]]
[[pause:0.8]]
[[voice:default]]
[[voice:low]]
[[voice:tense]]
[[voice:urgent]]

Rules:
- Put markers only inside full_narration.
- Do not put markers inside narration_segment.
- Do not put markers inside hook.
- Do not put markers inside title.
- Markers should be between scene narration pieces, not inside scene text.
- Use at most 4 pause markers.
- Use at most 3 voice markers.
- Never begin full_narration with a marker.

IMAGE PROMPT RULES:
Each image_prompt must match ONLY that scene's narration_segment.
Each image_prompt must include:
- exact subject
- exact action
- historical period/location
- visible objects
- clothing/architecture
- mood and lighting
- camera framing
- vertical 9:16 composition
- dark historical graphic-novel believability
- no text
- no watermark
- no subtitles
- no logos
- no modern objects
- Must show: ...
- Must not show: text, watermark, modern objects, gore, blood, nudity

OUTPUT RULES:
Return ONLY valid JSON.
No markdown.
No explanation.
No extra text.
"""


def _series_master_system_prompt() -> str:
    return _base_system_prompt(is_series=True) + f"""

SERIES MASTER-STORY MODE:
You are NOT writing isolated episodes first.
You must first think as a showrunner.
You must create ONE complete chronological story spine, then divide that story into episodes.

This prevents missing connective events.
The series must feel like one continuous film cut into parts.

MASTER STORY RULES:
- Build a chronological event timeline first (30–50 events).
- Do not skip causal events.
- Do not jump from a cliffhanger directly to a later consequence.
- Every event must cause the next event.
- Every episode must receive a specific assigned timeline section.
- Each episode script must use ONLY its assigned timeline events.
- The first scene of each episode must continue exactly from the previous episode's final event.
- The final scene of each episode must set up the next episode's first event.
- STORY DENSITY: Each episode MUST have 3–5 meaningful story beats.
- NO FILLER: Do not maximize episode count. If an episode has fewer than 3 beats, it MUST be merged.
- ENGAGEMENT: Every episode must have a strong hook, tension, and a reason to exist.
- PACING: Choose the smallest number of episodes that preserves the story and keeps every part exciting.
- EPISODE COUNT: If resolved_episode_count is N, you MUST return exactly N episode_plans, numbered 1 to N.
- DISTRIBUTION: Distribute master_timeline events across all episodes. Do not place all events into episode 1. Each episode should receive 3–6 meaningful events.
- COMPACTNESS: Do NOT copy long research paragraphs, source notes, or URLs. Use concise IDs and short fields. Research is for internal grounding ONLY.

EXACT INCIDENT CONTINUITY:
- The next episode MUST continue the exact previous incident before moving to any new arc.
- If the previous final_scene leaves a character at a specific place doing a specific thing, the next episode must begin at that same place and resolve it.
- Only after resolving the previous incident in scenes 1–3 may the new arc begin.

CITATION RULES:
- DO NOT include visible citations, source notes, URLs, or academic references in the script or narration.
- Research is for internal grounding only.

Bad opening. Do NOT do this:
"Part 3. Hannibal vanished, but Rome never forgot Carthage's crime."

Good opening:
"Part 3. Hannibal stood near Rome. But the walls still held."
"He had no siege machines. His army was exhausted. And Carthage sent no help."

If a previous episode ends with Hannibal near Rome but not taking the city, the next episode must explain why before moving to revenge:
- Rome's walls still held.
- Rome refused to surrender.
- Hannibal lacked siege machines.
- His army was exhausted and far from supplies.
- Carthage did not send enough support.
- Rome avoided the battles Hannibal wanted.
- Rome later attacked Africa, forcing Hannibal home.

EPISODE CTA RULES:
- Every episode except the final known part ends with exactly: "Follow for part X."
- The final known part ends with exactly: "Follow for the next dark history story."
- CTA appears only once, inside the final scene.
- The final scene is never CTA-only.
"""


# -----------------------------------------------------------------------------
# Repair helpers
# -----------------------------------------------------------------------------

def _hard_fail_issues(issues: list[str]) -> list[str]:
    hard_patterns = [
        "empty image_prompt",
        "empty or punctuation-only",
        "Final scene is CTA-only",
        "repeat the same final bridge line",
        "Too many scenes share",
        "Duplicate narration segment",
    ]
    return [issue for issue in issues if any(p in issue for p in hard_patterns)]


def _repair_script_with_gpt(
    script: dict,
    topic: dict,
    issues: list[str],
    previous_script: dict | None,
    next_topic: dict | None,
    research_context: str = "",
) -> dict:
    previous_context = (
        json.dumps(_compact_script_for_context(previous_script), indent=2, ensure_ascii=False)
        if previous_script else "null"
    )
    next_context = json.dumps(next_topic, indent=2, ensure_ascii=False) if next_topic else "null"

    is_series = _is_series_topic(topic)
    system_prompt = _base_system_prompt(is_series=is_series) + """

You are now repairing a script that failed validation.
Return the same JSON script shape only.
Do not add explanations.
Fix every issue listed.
Preserve the episode's topic, series, part, CTA, 12-scene structure, and JSON keys.
Rewrite only what is needed, but if repeated scenes exist, rewrite those scenes fully.
"""

    research_block = f"""
Verified research context (use to fix factual accuracy — do NOT cite sources in script):
{research_context}
""" if research_context else ""

    user_prompt = f"""
Topic:
{json.dumps(topic, indent=2, ensure_ascii=False)}

Previous episode context:
{previous_context}

Next topic:
{next_context}
{research_block}
Validation issues to fix:
{json.dumps(issues, indent=2, ensure_ascii=False)}

Broken script JSON:
{json.dumps(script, indent=2, ensure_ascii=False)}

Hard examples:

For Part 3, bad:
"Part 3. Hannibal stood at Rome's gates. But the city did not fall. Rome survived. Carthage was forced to retreat."

For Part 3, good progression:
Scene 1: "Part 3. Hannibal stood near Rome. But the walls still held."
Scene 2: "He had no siege machines, and his army was exhausted."
Scene 3: "Carthage sent no help, so Hannibal could only wait."
Scene 4: "Rome refused to surrender and learned how to survive him."
Scene 5: "Then Rome struck Africa, forcing Hannibal home."
Only after this may the script move into Rome's revenge.

For Part 7, bad:
Repeating "The last defenders gathered at the temple" for scenes 8–12.

For Part 7, good progression:
Scene 8: "Block by block, Rome forced the survivors uphill."
Scene 9: "Families and fighters crowded toward the sacred temple."
Scene 10: "The temple doors became Carthage's final shield."
Scene 11: "Behind them, the city disappeared into smoke."
Scene 12: "Inside those walls, the last stand was waiting. Follow for part 8."

Return ONLY the corrected script JSON, not a wrapper.
"""

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=6000,
        temperature=0.45,
        reasoning_effort="low",
    )

    try:
        return _extract_json(raw, prefix="quality_repair")
    except Exception as e:
        print(f"⚠️  Quality repair JSON is malformed. Attempting one-time structural repair... (Error: {e})")
        repair_system = (
            "You are a JSON repair specialist. Repair the provided malformed JSON "
            "into strictly valid JSON matching the requested schema. "
            "Return ONLY the valid JSON."
        )
        repair_user = f"Malformed JSON to fix:\n\n{raw}"
        try:
            repaired_raw = _openai_text_response(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=SCRIPT_MODEL,
                max_output_tokens=6000,
                reasoning_effort="low"
            )
            return _extract_json(repaired_raw, prefix="quality_repair_repaired")
        except Exception as e2:
            raise Exception(f"Failed to generate or repair quality repair JSON: {e}") from e2


def _postprocess_validate_repair(
    script: dict,
    topic: dict,
    previous_script: dict | None = None,
    next_topic: dict | None = None,
    *,
    max_repairs: int = 2,
    research_context: str = "",
) -> dict:
    current = _postprocess_script(script, topic)

    last_issues = []
    for attempt in range(1, max_repairs + 1):
        issues = _validate_script_quality_strict(current, topic, previous_script, next_topic)
        if not issues:
            return current

        # Check if we are stuck in a loop with the same issues
        if issues == last_issues:
            print(f"   ⚠️ Issues persisted after repair attempt {attempt-1}. Continuing to avoid loop.")
            break
        last_issues = issues

        print(f"   🔧 Script repair attempt {attempt}/{max_repairs}: {len(issues)} issue(s)")
        for issue in issues[:10]:
            print(f"      - {issue}")

        current = _repair_script_with_gpt(
            current,
            topic,
            issues,
            previous_script,
            next_topic,
            research_context=research_context,
        )
        current = _postprocess_script(current, topic)

    final_issues = _validate_script_quality_strict(current, topic, previous_script, next_topic)
    if final_issues:
        print(f"   ⚠️ Script still has {len(final_issues)} issue(s) after repairs:")
        for issue in final_issues:
            print(f"      - {issue}")

        hard = _hard_fail_issues(final_issues)
        if hard:
            raise Exception(
                "Script has hard validation failures after repair: " + "; ".join(hard[:6])
            )
        else:
            print("   ✅ Continuing despite soft warnings.")

    return current


# -----------------------------------------------------------------------------
# Public functions
# -----------------------------------------------------------------------------

def generate_script(topic: dict) -> dict:
    """Generate one standalone or single-topic script."""
    is_series = _is_series_topic(topic)
    series_context = _series_context_for_single_topic(topic)
    system_prompt = _base_system_prompt(is_series=is_series)

    # Load series research for grounding (does not block if file is absent).
    research: dict | None = None
    if is_series:
        series_name = str(topic.get("series", "") or "").strip()
        series_topics_list = _load_series_topics(series_name)
        research = ensure_series_research(series_name, series_topics_list, rebuild=False)
    research_context = _format_research_context(research)
    research_block = f"\n{research_context}\n" if research_context else ""

    if is_series:
        target_runtime_text = f"about {TARGET_RUNTIME_SECONDS} seconds"
        target_words_text = f"{TARGET_WORDS_LOW}–{TARGET_WORDS_HIGH} words"
        target_scenes_text = f"Exactly {TARGET_SCENES} scenes"
    else:
        target_runtime_text = "about 55–70 seconds (no 90-second scripts unless explicitly requested)"
        target_words_text = "around 125–155 words"
        target_scenes_text = f"exactly {TARGET_SCENES} scenes (unless the script is very short and clearly needs fewer, e.g. 8–9 scenes). Do NOT stretch the story with filler"

    user_prompt = f"""
Create one short cinematic history video script.

Topic title:
{topic["title"]}

Description / angle:
{topic["description"]}

{series_context}
{research_block}
CONTEXT ANCHOR RULES (apply only when research is provided above):
- When introducing a person for the first time, briefly say who they are.
- When mentioning a place for the first time, briefly say where it is.
- Include dates or time gaps when they add meaning (e.g. "fifty years later").
- Show cause-and-effect links naturally in the narration.
- Do NOT include academic citations or source names in the script output.

TARGET:
- Runtime: {target_runtime_text}.
- Total narration: {target_words_text}.
- Scenes: {target_scenes_text}.
- One clear visual beat per scene.
- Each narration_segment should usually be 8–16 spoken words.
- No rapid-fire scene cuts.
- No school-lesson tone.
- The story should feel like one cinematic trailer, not a lecture.

Return ONLY this exact JSON structure:

{{
  "title": "Compelling title under 60 characters",
  "hook": "The exact opening hook, short and suspenseful, no markers",
  "full_narration": "Full narration made from all narration_segment values in order, with optional performance markers between sentences only.",
  "scenes": [
    {{
      "scene_number": 1,
      "timestamp_start": 0,
      "timestamp_end": 6,
      "narration_segment": "Exact spoken narration for this one visual beat only, no markers",
      "image_prompt": "Scene-locked image prompt matching only this narration segment, including Must show and Must not show",
      "scene_description": "One sentence describing the visual"
    }}
  ],
  "hashtags": ["ten", "short", "relevant", "keywords", "without", "hash", "symbols", "about", "the", "topic"],
  "youtube_description": "A short natural description, around 90–130 words.",
  "cta": "A short CTA that fits the dramatic tone."
}}

Now write the complete JSON.
"""

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=6000,
        temperature=0.65,
        reasoning_effort="medium",
    )

    try:
        script = _extract_json(raw, prefix="episode_script")
    except Exception as e:
        print(f"⚠️  Episode script JSON is malformed. Attempting one-time repair... (Error: {e})")
        repair_system = (
            "You are a JSON repair specialist. Repair the provided malformed JSON "
            "into strictly valid JSON matching the requested schema. "
            "Do NOT add new facts. Do NOT summarize. Return ONLY the valid JSON."
        )
        repair_user = f"Malformed JSON to fix:\n\n{raw}"
        try:
            repaired_raw = _openai_text_response(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=SCRIPT_MODEL,
                max_output_tokens=6000,
                reasoning_effort="low"
            )
            script = _extract_json(repaired_raw, prefix="episode_script_repaired")
            print("✅ Episode script JSON repaired successfully.")
        except Exception as e2:
            raise Exception(f"Failed to generate or repair episode script JSON: {e}") from e2
    previous_script = _get_previous_episode_script(topic)
    next_topic = _next_episode_topic(topic)
    script = _postprocess_validate_repair(
        script, topic, previous_script, next_topic,
        research_context=research_context,
    )

    script["topic_id"] = topic["id"]
    script["series"] = topic.get("series", "")
    script["part"] = topic.get("part", 0)
    if research:
        script["research_hash"] = get_research_hash(research)

    print(f"✅ Script generated: {script['title']}")
    print(f"   Hook: {script['hook']}")
    print(f"   Scenes: {len(script['scenes'])}")
    print(f"   Target runtime: ~{TARGET_RUNTIME_SECONDS}s")
    print(f"   Target words: {TARGET_WORDS_LOW}-{TARGET_WORDS_HIGH}")

    if _is_series_topic(topic):
        print(f"   Series: {topic.get('series')} — Part {topic.get('part')}")
        print(f"   Series CTA: {script.get('cta')}")
        if research:
            print(f"   Research hash: {script.get('research_hash')}")

    if "[[" in script.get("full_narration", ""):
        print("   🎭 Voice performance markers inserted in full_narration")

    return script


def _generate_episode_script_from_timeline(
    *,
    episode_plan: dict,
    series_name: str,
    total_parts: int,
    master_timeline: list[dict],
    timeline_events: list[dict],
    previous_script: dict | None,
    next_topic: dict | None,
    research_context: str = "",
) -> dict:
    part_num = int(episode_plan.get("episode_number", 1))
    
    # We construct a fake topic dict to reuse _series_cta
    fake_topic = {"series": series_name, "part": part_num}
    if total_parts:
        fake_topic["episode_count"] = total_parts
    cta = _series_cta(fake_topic)

    previous_context = (
        json.dumps(_compact_script_for_context(previous_script), indent=2, ensure_ascii=False)
        if previous_script else "null"
    )
    next_context = json.dumps(next_topic, indent=2, ensure_ascii=False) if next_topic else "null"
    timeline_json = json.dumps(master_timeline, indent=2, ensure_ascii=False)
    assigned_json = json.dumps(timeline_events, indent=2, ensure_ascii=False)
    plan_json = json.dumps(episode_plan, indent=2, ensure_ascii=False)

    system_prompt = _base_system_prompt(is_series=True) + f"""

EPISODE-FROM-MASTER-TIMELINE MODE:
You are now writing exactly ONE episode from a prebuilt chronological master timeline and episode plan.

The master timeline and episode plan are the sources of truth.
You must follow the episode plan precisely.

EPISODE RULES:
- Write exactly {TARGET_SCENES} scenes.
- The episode should be about {TARGET_WORDS_LOW}-{TARGET_WORDS_HIGH} spoken words.
- Scene 1 must start with: "Part {part_num}."
- Final scene must end with exactly: "{cta}"
- CTA must appear once only, inside the final scene.
- No scene can be CTA-only.
- No duplicate narration segments.
- No repeated filler beats.
- Every scene must advance the episode.

MASTER TIMELINE RULES:
- Use ONLY the assigned timeline events for this episode.
- Mention each assigned event clearly in order.
- If the previous episode left an unresolved action, resolve it physically and causally in scenes 1-3.
- The final two scenes must point naturally toward the next topic.
"""

    user_prompt = f"""
Series name:
{series_name}

Total known parts:
{total_parts}

Current episode plan:
{plan_json}

Previous episode context:
{previous_context}

Next episode context:
{next_context}

Full master timeline for context:
{timeline_json}

Assigned timeline events for THIS episode:
{assigned_json}

Verified research context:
{research_context if research_context else "No research context provided."}

Return ONLY this exact JSON script shape:

{{
  "title": "Compelling title under 60 characters",
  "hook": "The exact opening hook, short and suspenseful, no markers",
  "full_narration": "Full narration made from all narration_segment values in order, with optional performance markers between sentences only.",
  "scenes": [
    {{
      "scene_number": 1,
      "timestamp_start": 0,
      "timestamp_end": 6,
      "narration_segment": "Exact spoken narration for this one visual beat only, no markers",
      "image_prompt": "Scene-locked image prompt matching only this narration segment",
      "scene_description": "One sentence describing the visual"
    }}
  ],
  "hashtags": ["ten", "short", "relevant", "keywords", "without", "hash", "symbols", "about", "the", "topic"],
  "youtube_description": "A short natural description, around 90–130 words.",
  "cta": "{cta}"
}}
"""

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=6500,
        temperature=0.62,
        reasoning_effort="medium",
    )

    try:
        return _extract_json(raw, prefix="script_repair")
    except Exception as e:
        print(f"⚠️  Script repair JSON is malformed. Attempting one-time repair... (Error: {e})")
        repair_system = (
            "You are a JSON repair specialist. Repair the provided malformed JSON "
            "into strictly valid JSON matching the requested schema. "
            "Return ONLY the valid JSON."
        )
        repair_user = f"Malformed JSON to fix:\n\n{raw}"
        try:
            repaired_raw = _openai_text_response(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=SCRIPT_MODEL,
                max_output_tokens=6500,
                reasoning_effort="low"
            )
            return _extract_json(repaired_raw, prefix="script_repair_repaired")
        except Exception as e2:
            raise Exception(f"Failed to generate or repair script repair JSON: {e}") from e2


def _validate_series_package(payload: dict, expected_count: int, master_timeline_len: int) -> list[str]:
    """Validate the structural integrity of the series package."""
    issues = []
    
    # 1. resolved_episode_count
    res_count = payload.get("resolved_episode_count")
    if res_count is None:
        # Fallback if missing
        res_count = expected_count
    
    if not isinstance(res_count, int) or res_count < 1:
        issues.append(f"resolved_episode_count must be an integer >= 1 (got {res_count})")
    
    # 2. episode_plans
    plans = payload.get("episode_plans")
    if not isinstance(plans, list):
        issues.append("episode_plans must be a list")
        return issues # Cannot continue other checks
    
    # 3. count check
    if len(plans) != expected_count:
        issues.append(f"Expected {expected_count} episode plans, but got {len(plans)}")
    
    # 4. episode numbers and events
    plan_numbers = []
    all_assigned_ids = set()
    
    for i, plan in enumerate(plans):
        num = plan.get("episode_number")
        plan_numbers.append(num)
        
        event_ids = plan.get("assigned_timeline_event_ids") or plan.get("timeline_event_ids") or []
        if not event_ids:
            issues.append(f"Episode {num if num else i+1} has 0 assigned events")
        
        if expected_count > 1 and len(event_ids) == master_timeline_len:
             issues.append(f"Episode {num if num else i+1} received the entire master timeline")
             
        for eid in event_ids:
            all_assigned_ids.add(eid)
            
    # 5. numbering check
    expected_numbers = list(range(1, expected_count + 1))
    if sorted([n for n in plan_numbers if isinstance(n, int)]) != expected_numbers:
        issues.append(f"Episode numbers must be exactly {expected_numbers} (got {plan_numbers})")
        
    return issues


def generate_series_script_package(
    topic: dict,
    *,
    overwrite_all: bool = False,
    rebuild_research: bool = False,
) -> dict:
    if not _is_series_topic(topic):
        return generate_script(topic)

    series_name = str(topic.get("title", "") or "").strip()
    current_topic_id = str(topic.get("id"))

    # -------------------------------------------------------
    # STAGE 0: Ensure research story bible exists.
    # -------------------------------------------------------
    research = ensure_series_research(
        topic, rebuild=rebuild_research
    )
    research_context = _format_research_context(research)
    research_hash = get_research_hash(research)
    if research_hash:
        print(f"   📚 Series research loaded (hash: {research_hash})")
    else:
        print("   ⚠️  No research context available — proceeding without story bible.")

    # -------------------------------------------------------
    # STAGE 0.5: Resolve Episode Count
    # -------------------------------------------------------
    resolution = resolve_episode_count(topic, research_context)
    total_parts = resolution["resolved_episode_count"]
    series_scope_plan = resolution["series_scope_plan"]
    count_mode = get_episode_count_mode(topic)

    # Find locked episodes
    locked_previous_scripts: list[dict] = []
    target_episodes: list[int] = []

    for ep in range(1, total_parts + 1):
        if not overwrite_all:
            existing = _load_json_file(get_script_path(topic, episode_num=ep), default=None)
            if existing:
                locked_previous_scripts.append(_compact_script_for_context(existing))
                continue
        target_episodes.append(ep)

    locked_context = json.dumps(locked_previous_scripts, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------
    # STAGE 1: Generate Master Timeline and Episode Plans
    # ------------------------------------------------------------
    system_prompt = _series_master_system_prompt() + """

MASTER TIMELINE AND EPISODE PLANS:
For this call, return the master chronological story spine AND the specific episode plans.
Do not write full episode scripts in this response.

Return ONLY this JSON structure:

{{
  "series": "Series Name",
  "master_timeline": [
    {{
      "event_id": "E01",
      "date": "...",
      "event": "...",
      "why_it_matters": "..."
    }}
  ],
  "episode_plans": [
    {{
      "episode_number": 1,
      "episode_title": "...",
      "opening_question": "...",
      "episode_summary": "...",
      "assigned_timeline_event_ids": ["E01", "E02"],
      "factual_anchors_to_include": ["..."],
      "character_introductions": ["..."],
      "place_anchors": ["..."],
      "time_gap_to_explain": "...",
      "ending_bridge": "...",
      "must_not_imply": "..."
    }}
  ]
}}
"""

    user_prompt = f"""
Series name:
{series_name}

Total known parts (resolved_episode_count):
{total_parts}

Series Description:
{topic.get("description", "")}

Locked previous approved scripts:
{locked_context}

Verified research context:
{research_context if research_context else "No research context provided."}

Create a master timeline AND exactly {total_parts} episode plans for the missing target episodes: {target_episodes}.

Rules:
- You MUST return exactly {total_parts} objects in "episode_plans".
- Each plan must have a unique "episode_number" from 1 to {total_parts}.
- Distribute the master_timeline events across all episodes.
- Do not place all events into episode 1.
- Continue from locked previous scripts if they exist.
- Do not rewrite locked previous episodes.
- Assign every event to an episode.
- The final event of each episode must naturally set up the next episode.
"""

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=18000,
        temperature=0.55,
        reasoning_effort="medium",
    )

    try:
        payload = _extract_json(raw, prefix="series_package")
    except Exception as e:
        print(f"⚠️  Series package JSON is malformed. Attempting one-time repair... (Error: {e})")
        repair_system = (
            "You are a JSON repair specialist. Repair the provided malformed JSON "
            "into strictly valid JSON matching the requested schema. "
            "Do NOT add new facts. Do NOT summarize. Do NOT add commentary. "
            "Return ONLY the valid JSON script."
        )
        repair_user = f"Malformed JSON to fix:\n\n{raw}"
        try:
            repaired_raw = _openai_text_response(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=SCRIPT_MODEL,
                max_output_tokens=18000,
                reasoning_effort="low"
            )
            payload = _extract_json(repaired_raw, prefix="series_package_repaired")
            print("✅ Series package JSON repaired successfully.")
        except Exception as e2:
            raise Exception(
                f"Failed to generate or repair series package JSON.\n"
                f"  Primary error: {e}\n"
                f"  Repair error:  {e2}"
            ) from e2

    # ------------------------------------------------------------
    # STAGE 1.5: Validate and quality-repair the package
    # ------------------------------------------------------------
    master_timeline = payload.get("master_timeline", [])
    episode_plans = payload.get("episode_plans", [])
    
    validation_issues = _validate_series_package(payload, total_parts, len(master_timeline))
    
    if validation_issues:
        print(f"⚠️  Series package failed validation ({len(validation_issues)} issues). Attempting GPT repair...")
        for issue in validation_issues:
            print(f"      - {issue}")
            
        repair_system = (
            "You are a series showrunner. Repair the following series package JSON. "
            f"You selected {total_parts} episodes, but the plans are invalid. "
            f"Return exactly {total_parts} episode_plans, numbered 1 through {total_parts}. "
            "Distribute the master_timeline events across ALL episodes. "
            "Do NOT put all events into episode 1. "
            "Return ONLY the valid JSON."
        )
        repair_user = f"Validation issues:\n{json.dumps(validation_issues, indent=2)}\n\nOriginal Package:\n{json.dumps(payload, indent=2)}"
        
        try:
            repaired_raw = _openai_text_response(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=SCRIPT_MODEL,
                max_output_tokens=18000,
                reasoning_effort="medium"
            )
            payload = _extract_json(repaired_raw, prefix="series_package_quality_repair")
            master_timeline = payload.get("master_timeline", [])
            episode_plans = payload.get("episode_plans", [])
            
            # Final validation check
            validation_issues = _validate_series_package(payload, total_parts, len(master_timeline))
            if validation_issues:
                raise Exception(f"Invalid series package after repair: {total_parts} episodes requested but {len(episode_plans)} plans returned. Issues: {validation_issues}")
            print("✅ Series package repaired and validated successfully.")
        except Exception as e:
            raise Exception(f"Failed to repair invalid series package: {e}")

    # Print summary
    print(f"   ✅ Episode plan check: {len(episode_plans)} plans for {total_parts} episodes")
    for plan in sorted(episode_plans, key=lambda x: int(x.get("episode_number", 0))):
        num = plan.get("episode_number")
        event_ids = plan.get("assigned_timeline_event_ids") or plan.get("timeline_event_ids") or []
        print(f"      - Episode {num}: {len(event_ids)} assigned events")

    if not isinstance(master_timeline, list) or not master_timeline:
        raise Exception(f"Series master timeline returned no events: {payload}")
    if not isinstance(episode_plans, list) or not episode_plans:
        raise Exception(f"Series master timeline returned no episode_plans: {payload}")

    master_path = get_series_package_path(topic)
    payload["episode_count_mode"] = count_mode
    payload["resolved_episode_count"] = total_parts
    payload["min_episodes"] = get_min_episodes(topic)
    payload["max_episodes"] = get_max_episodes(topic)
    payload["series_scope_plan"] = series_scope_plan

    if research_hash:
        payload["research_hash"] = research_hash
    _save_json_file(master_path, payload)
    print(f"   🧭 Saved series package (timeline + plans): {master_path}")

    # ------------------------------------------------------------
    # STAGE 2: Generate each target episode separately
    # ------------------------------------------------------------
    saved_by_id: dict[int, dict] = {}

    previous_full_script_by_part: dict[int, dict] = {}
    for locked in locked_previous_scripts:
        locked_part = int(locked.get("part", 0) or 0)
        if locked_part > 0:
            full_locked = _load_json_file(get_script_path(topic, episode_num=locked_part), default=None)
            if full_locked:
                previous_full_script_by_part[locked_part] = full_locked

    for episode_plan in sorted(episode_plans, key=lambda x: int(x.get("episode_number", 0))):
        episode_part = int(episode_plan.get("episode_number", 0))
        if episode_part not in target_episodes:
            continue

        assigned_event_ids = episode_plan.get("assigned_timeline_event_ids", [])
        timeline_events = [e for e in master_timeline if e.get("event_id") in assigned_event_ids]

        if not timeline_events:
            timeline_events = [
                {
                    "event_id": f"P{episode_part}_FALLBACK",
                    "event": str(episode_plan.get("episode_summary", "")),
                    "why_it_matters": "This topic must still be produced.",
                }
            ]

        previous_script = previous_full_script_by_part.get(episode_part - 1)
        next_topic = None # Not strictly used now, but we can leave it None

        print(
            f"   📝 Generating episode script from timeline: "
            f"topic {current_topic_id}, part {episode_part}, events={len(timeline_events)}"
        )

        script_obj = _generate_episode_script_from_timeline(
            episode_plan=episode_plan,
            series_name=series_name,
            total_parts=total_parts,
            master_timeline=master_timeline,
            timeline_events=timeline_events,
            previous_script=previous_script,
            next_topic=next_topic,
            research_context=research_context,
        )

        # We construct a fake topic for postprocessing
        fake_topic = topic.copy()
        fake_topic["part"] = episode_part
        fake_topic["episode_plan"] = episode_plan

        script_obj = _postprocess_validate_repair(
            script_obj,
            fake_topic,
            previous_script=previous_script,
            next_topic=next_topic,
            research_context=research_context,
        )

        script_obj["topic_id"] = current_topic_id
        script_obj["series"] = series_name
        script_obj["part"] = episode_part
        script_obj["episode_number"] = episode_part
        script_obj["resolved_episode_count"] = total_parts
        script_obj["episode_count_mode"] = count_mode
        script_obj["timeline_event_ids"] = assigned_event_ids
        if research_hash:
            script_obj["research_hash"] = research_hash

        path = get_script_path(topic, episode_num=episode_part)
        _save_json_file(path, script_obj)
        saved_by_id[episode_part] = script_obj
        previous_full_script_by_part[episode_part] = script_obj
        print(f"   ✅ Saved connected script: {path}")

    # Return the first target episode if available
    first_target = target_episodes[0] if target_episodes else 1
    selected = saved_by_id.get(first_target)
    if selected:
        print(f"✅ Connected series package generated for {series_name}")
        return selected

    existing_current = _load_json_file(get_script_path(topic, episode_num=1), default=None)
    if existing_current:
        return existing_current

    raise Exception(f"Series package did not produce any scripts for {series_name}")


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower())
    return value.strip("_") or "series"


def generate_thumbnail_prompt(script: dict) -> str:
    """Generate a specific prompt for the thumbnail image."""
    system_prompt = (
        "You write premium GPT Image prompts for dark cinematic history thumbnails. "
        "Output only the prompt."
    )

    user_prompt = f"""
Create a thumbnail image prompt for this dark cinematic history video.

Title:
{script['title']}

Hook:
{script['hook']}

The thumbnail should:
- Look like a dark historical movie poster / graphic novel poster
- Have one clear focal subject
- Be readable instantly
- Communicate the topic within one second
- Use dramatic but visible lighting
- Use smoke, firelight, gold, shadows, or blood-red tones only if appropriate
- Work as a 1280x720 YouTube thumbnail
- Be cinematic, stylized, and premium
- Contain no text
- Contain no watermark
- Contain no logos
- Contain no modern objects

Return ONLY the image generation prompt, nothing else.
"""

    return _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=350,
        temperature=0.55,
        reasoning_effort="medium",
    ).strip()
