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
TARGET_SCENES = 12
TARGET_WORDS_LOW = 120
TARGET_WORDS_HIGH = 155

PERFORMANCE_MARKER_RE = re.compile(
    r"\[\[(pause|voice):([^\]]+)\]\]",
    re.IGNORECASE,
)

CTA_RE = re.compile(
    r"\bFollow for (?:part\s+\d+|more dark history|the next dark history story)\.??",
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


def _openai_text_response(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_output_tokens: int,
    temperature: float | None = 0.65,
) -> str:
    """
    Calls OpenAI Responses API for text generation.

    Some models reject temperature. If so, retry once without it.
    """
    if not str(OPENAI_API_KEY or "").strip():
        raise Exception("Set OPENAI_API_KEY in config.py for GPT script generation.")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

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

    if temperature is not None:
        payload["temperature"] = temperature

    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers=headers,
        json=payload,
        timeout=300,
    )

    if response.status_code == 400 and "temperature" in response.text.lower():
        payload.pop("temperature", None)
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers=headers,
            json=payload,
            timeout=300,
        )

    if response.status_code != 200:
        raise Exception(
            f"OpenAI text error {response.status_code}: {response.text[:2000]}"
        )

    return _extract_response_text(response.json())


def _extract_json(raw: str) -> dict:
    """Extract JSON even if GPT wraps it in fences."""
    raw = raw.strip()

    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
        raw = raw.strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(raw[start:end + 1])


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

def _topic_part(topic: dict) -> int:
    try:
        return int(topic.get("part", 0) or 0)
    except ValueError:
        return 0


def _is_series_topic(topic: dict) -> bool:
    series_name = str(topic.get("series", "") or "").strip()
    return bool(series_name) and _topic_part(topic) > 0


def _series_total_parts(series_name: str) -> int | None:
    series_name = str(series_name or "").strip()
    if not series_name or not os.path.isfile(TOPICS_FILE):
        return None

    max_part = 0
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if str(row.get("series", "")).strip() != series_name:
                    continue
                max_part = max(max_part, _topic_part(row))
    except Exception:
        return None

    return max_part if max_part > 0 else None


def _series_cta(topic: dict) -> str:
    series_name = str(topic.get("series", "") or "").strip()
    part_num = _topic_part(topic)

    if not series_name or part_num <= 0:
        return "Follow for more dark history."

    total_parts = _series_total_parts(series_name)
    if total_parts is not None and part_num >= total_parts:
        return "Follow for the next dark history story."

    return f"Follow for part {part_num + 1}."


def _topic_script_path(topic_id: str | int) -> str:
    return os.path.join(OUTPUT_DIR, f"topic_{topic_id}_script.json")


def _load_json_file(path: str, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_series_topics(series_name: str) -> list[dict]:
    if not os.path.isfile(TOPICS_FILE):
        raise FileNotFoundError(f"Topics file not found: {TOPICS_FILE}")

    rows: list[dict] = []
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("series", "")).strip() != series_name:
                continue
            if _topic_part(row) <= 0:
                continue
            rows.append(row)

    rows.sort(key=_topic_part)
    return rows


def _previous_episode_topic(topic: dict) -> dict | None:
    if not _is_series_topic(topic):
        return None

    series_name = str(topic.get("series", "") or "").strip()
    current_part = _topic_part(topic)
    for row in _load_series_topics(series_name):
        if _topic_part(row) == current_part - 1:
            return row
    return None


def _next_episode_topic(topic: dict) -> dict | None:
    if not _is_series_topic(topic):
        return None

    series_name = str(topic.get("series", "") or "").strip()
    current_part = _topic_part(topic)
    for row in _load_series_topics(series_name):
        if _topic_part(row) == current_part + 1:
            return row
    return None


def _get_previous_episode_script(topic: dict) -> dict | None:
    prev_topic = _previous_episode_topic(topic)
    if not prev_topic:
        return None
    return _load_json_file(_topic_script_path(prev_topic.get("id", "")), default=None)


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
    part_num = _topic_part(topic)
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


def _normalize_scene_timestamps(script: dict) -> dict:
    scenes = script.get("scenes", []) or []
    if not scenes:
        return script

    scene_count = len(scenes)
    seconds_per_scene = TARGET_RUNTIME_SECONDS / max(scene_count, 1)

    for i, scene in enumerate(scenes, start=1):
        scene["scene_number"] = i
        scene["timestamp_start"] = round((i - 1) * seconds_per_scene, 2)
        scene["timestamp_end"] = round(i * seconds_per_scene, 2)

    scenes[-1]["timestamp_end"] = TARGET_RUNTIME_SECONDS
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

    prev_scenes = previous_script.get("scenes", []) or []
    prev_text = " ".join(scene.get("narration_segment", "") for scene in prev_scenes[-3:])
    prev_text_lower = prev_text.lower()

    first_four = " ".join(
        scene.get("narration_segment", "")
        for scene in (script.get("scenes", []) or [])[:4]
    ).lower()

    hannibal_unresolved = (
        "hannibal" in prev_text_lower
        and (
            "never took" in prev_text_lower
            or "gates" in prev_text_lower
            or "city" in prev_text_lower
        )
    )

    if hannibal_unresolved:
        concepts = [
            "wall",
            "walls",
            "siege machine",
            "siege machines",
            "supplies",
            "supply",
            "exhausted",
            "tired",
            "no help",
            "sent no help",
            "refused to surrender",
            "could not storm",
            "africa",
            "called home",
            "forced home",
        ]
        hits = sum(1 for concept in concepts if concept in first_four)
        if hits < 2:
            issues.append(
                "Part 3 must explain why Hannibal did not take Rome before moving into revenge."
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
    part = _topic_part(topic)

    if len(scenes) != TARGET_SCENES:
        issues.append(f"Expected {TARGET_SCENES} scenes, got {len(scenes)}")

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
            issues.append("Scene 11 and 12 repeat the same final bridge line")

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
    script = _normalize_scene_timestamps(script)

    script["title"] = _clean_text_spacing(script.get("title", ""))[:80]
    scenes = script.get("scenes", []) or []
    if scenes:
        script["hook"] = _meaningful_hook_from_scene1(
            scenes[0].get("narration_segment", ""),
            _is_series_topic(topic),
            _topic_part(topic),
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

    part_num = _topic_part(topic)
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


def _base_system_prompt() -> str:
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
- Dark tension
- Clear cause and effect
- Story first, facts second
- One visual idea at a time
- Emotional stakes
- Strong hook
- Strong final line

TONE SAFETY:
- Keep the tone dark and cinematic, never graphic.
- Do NOT use phrases like: "rivers of blood", "bodies filled the streets", "massacre", "no one was spared", "slaughter", "gore", "dismembered".
- Prefer cinematic phrasings like: "the streets became silent", "the city broke under the assault", "smoke swallowed the ruins", "survivors vanished into the shadows".
- Image prompts must avoid blood, gore, dismemberment, explicit injury, and nudity.
- Image prompt "Must not show:" should include: text, watermark, modern objects, gore, blood, nudity.

SCENE RULES:
- Use exactly {TARGET_SCENES} scenes.
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
    return _base_system_prompt() + f"""

SERIES MASTER-STORY MODE:
You are NOT writing isolated episodes first.
You must first think as a showrunner.
You must create ONE complete chronological story spine, then divide that story into episodes.

This prevents missing connective events.
The series must feel like one continuous film cut into parts.

MASTER STORY RULES:
- Build a chronological event timeline first.
- Do not skip causal events.
- Do not jump from a cliffhanger directly to a later consequence.
- Every event must cause the next event.
- Every episode must receive a specific assigned timeline section.
- Each episode script must use ONLY its assigned timeline events.
- The first scene of each episode must continue exactly from the previous episode's final event.
- The final scene of each episode must set up the next episode's first event.

EXACT INCIDENT CONTINUITY:
- The next episode MUST continue the exact previous incident before moving to any new arc.
- If the previous final_scene leaves a character at a specific place doing a specific thing, the next episode must begin at that same place and resolve it.
- Only after resolving the previous incident in scenes 1–3 may the new arc begin.

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
        "Scene 11 and 12 repeat",
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
) -> dict:
    previous_context = (
        json.dumps(_compact_script_for_context(previous_script), indent=2, ensure_ascii=False)
        if previous_script else "null"
    )
    next_context = json.dumps(next_topic, indent=2, ensure_ascii=False) if next_topic else "null"

    system_prompt = _base_system_prompt() + """

You are now repairing a script that failed validation.
Return the same JSON script shape only.
Do not add explanations.
Fix every issue listed.
Preserve the episode's topic, series, part, CTA, 12-scene structure, and JSON keys.
Rewrite only what is needed, but if repeated scenes exist, rewrite those scenes fully.
"""

    user_prompt = f"""
Topic:
{json.dumps(topic, indent=2, ensure_ascii=False)}

Previous episode context:
{previous_context}

Next topic:
{next_context}

Validation issues to fix:
{json.dumps(issues, indent=2, ensure_ascii=False)}

Broken script JSON:
{json.dumps(script, indent=2, ensure_ascii=False)}

Hard examples:

For Part 3, bad:
"Part 3. Hannibal stood at Rome’s gates. But the city did not fall. Rome survived. Carthage was forced to retreat."

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
Scene 10: "The temple doors became Carthage’s final shield."
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
    )

    return _extract_json(raw)


def _postprocess_validate_repair(
    script: dict,
    topic: dict,
    previous_script: dict | None = None,
    next_topic: dict | None = None,
    *,
    max_repairs: int = 2,
) -> dict:
    current = _postprocess_script(script, topic)

    for attempt in range(1, max_repairs + 1):
        issues = _validate_script_quality_strict(current, topic, previous_script, next_topic)
        if not issues:
            return current

        print(f"   🔧 Script repair attempt {attempt}/{max_repairs}: {len(issues)} issue(s)")
        for issue in issues[:10]:
            print(f"      - {issue}")

        current = _repair_script_with_gpt(
            current,
            topic,
            issues,
            previous_script,
            next_topic,
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

    return current


# -----------------------------------------------------------------------------
# Public functions
# -----------------------------------------------------------------------------

def generate_script(topic: dict) -> dict:
    """Generate one standalone or single-topic script."""
    series_context = _series_context_for_single_topic(topic)
    system_prompt = _base_system_prompt()

    user_prompt = f"""
Create one short cinematic history video script.

Topic title:
{topic["title"]}

Description / angle:
{topic["description"]}

{series_context}

TARGET:
- Runtime: about {TARGET_RUNTIME_SECONDS} seconds.
- Total narration: {TARGET_WORDS_LOW}–{TARGET_WORDS_HIGH} words.
- Exactly {TARGET_SCENES} scenes.
- One clear visual beat per scene.
- Each narration_segment should usually be 8–16 spoken words.
- No rapid-fire scene cuts.
- No school-lesson tone.
- The story should feel like one cinematic trailer, not a lecture.

Return ONLY this exact JSON structure:

{{
  "title": "Compelling title under 60 characters",
  "hook": "The exact opening hook, short and suspenseful, no markers",
  "full_narration": "Full narration made from all narration_segment values in order, with optional performance markers between sentences only. {TARGET_WORDS_LOW}–{TARGET_WORDS_HIGH} words total.",
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
        max_output_tokens=3500,
        temperature=0.65,
    )

    script = _extract_json(raw)
    previous_script = _get_previous_episode_script(topic)
    next_topic = _next_episode_topic(topic)
    script = _postprocess_validate_repair(script, topic, previous_script, next_topic)

    script["topic_id"] = topic["id"]
    script["series"] = topic.get("series", "")
    script["part"] = topic.get("part", 0)

    print(f"✅ Script generated: {script['title']}")
    print(f"   Hook: {script['hook']}")
    print(f"   Scenes: {len(script['scenes'])}")
    print(f"   Target runtime: ~{TARGET_RUNTIME_SECONDS}s")
    print(f"   Target words: {TARGET_WORDS_LOW}-{TARGET_WORDS_HIGH}")

    if _is_series_topic(topic):
        print(f"   Series: {topic.get('series')} — Part {topic.get('part')}")
        print(f"   Series CTA: {script.get('cta')}")

    if "[[" in script.get("full_narration", ""):
        print("   🎭 Voice performance markers inserted in full_narration")

    return script


def _generate_episode_script_from_timeline(
    *,
    episode_topic: dict,
    series_name: str,
    total_parts: int,
    master_timeline: list[dict],
    timeline_events: list[dict],
    previous_script: dict | None,
    next_topic: dict | None,
) -> dict:
    """
    Generate ONE episode from the already-approved master timeline.

    This is intentionally separate from master timeline generation. The model first
    decides the whole chronological story spine, then writes each episode from only
    that episode's assigned events. This prevents the model from cutting connective
    events just to satisfy an episode title.
    """
    part_num = _topic_part(episode_topic)
    cta = _series_cta(episode_topic)

    previous_context = (
        json.dumps(_compact_script_for_context(previous_script), indent=2, ensure_ascii=False)
        if previous_script else "null"
    )
    next_context = json.dumps(next_topic, indent=2, ensure_ascii=False) if next_topic else "null"
    timeline_json = json.dumps(master_timeline, indent=2, ensure_ascii=False)
    assigned_json = json.dumps(timeline_events, indent=2, ensure_ascii=False)

    system_prompt = _base_system_prompt() + f"""

EPISODE-FROM-MASTER-TIMELINE MODE:
You are now writing exactly ONE episode from a prebuilt chronological master timeline.

The master timeline is the source of truth.
You must not skip the assigned timeline events.
You must not jump to a later consequence before resolving the earlier cause.
You must not summarize over an important event that belongs to this episode.

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
- The final 4 scenes must be distinct.

MASTER TIMELINE RULES:
- Use ONLY the assigned timeline events for this episode.
- Mention each assigned event clearly in order.
- If the previous episode left an unresolved action, resolve it physically and causally in scenes 1-3.
- If this episode begins after a cliffhanger, do not move to the next arc until the cliffhanger is answered.
- The final two scenes must point naturally toward the next topic.

EXACT INCIDENT EXAMPLE:
If the episode begins after Hannibal reached Rome but did not take the city, do NOT write:
"Part 3. Rome never forgot."
Instead, explain the incident first:
"Part 3. Hannibal stood near Rome. But the walls still held."
"He had no siege machines, and his army was exhausted."
"Carthage sent no help, so Hannibal could only wait."
Only then move to Rome attacking Africa, Hannibal returning home, Zama, and Rome's long revenge.
"""

    user_prompt = f"""
Series name:
{series_name}

Total known parts:
{total_parts}

Current episode topic:
{json.dumps(episode_topic, indent=2, ensure_ascii=False)}

Previous episode context:
{previous_context}

Next episode topic:
{next_context}

Full master timeline for context:
{timeline_json}

Assigned timeline events for THIS episode:
{assigned_json}

Write ONLY this episode's script.
Do not write scripts for other episodes.
Do not return a wrapper.
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
      "image_prompt": "Scene-locked image prompt matching only this narration segment, including Must show and Must not show",
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
    )

    return _extract_json(raw)


def generate_series_script_package(
    topic: dict,
    *,
    overwrite_all: bool = False,
) -> dict:
    """
    Generate a whole connected series package using a two-stage architecture:

    1. Generate one master chronological timeline for the target episodes.
    2. Generate each episode separately from its assigned timeline events.

    This fixes the problem where one huge response only returned Part 3, or where
    the model cut connective events because it was thinking episode-by-episode.
    """
    if not _is_series_topic(topic):
        return generate_script(topic)

    series_name = str(topic.get("series", "") or "").strip()
    current_part = _topic_part(topic)
    current_topic_id = str(topic.get("id"))
    series_topics = _load_series_topics(series_name)

    if not series_topics:
        raise Exception(f"No topics found for series: {series_name}")

    locked_previous_scripts: list[dict] = []
    target_topics: list[dict] = []

    for row in series_topics:
        part = _topic_part(row)
        if not overwrite_all and part < current_part:
            existing = _load_json_file(_topic_script_path(row["id"]), default=None)
            if existing:
                locked_previous_scripts.append(_compact_script_for_context(existing))
            continue
        target_topics.append(row)

    if not target_topics:
        existing_current = _load_json_file(_topic_script_path(current_topic_id), default=None)
        if existing_current:
            return existing_current
        raise Exception("No target topics to generate and current script cache is missing.")

    locked_context = json.dumps(locked_previous_scripts, indent=2, ensure_ascii=False)

    full_series_list = json.dumps(
        [
            {
                "id": row.get("id"),
                "part": row.get("part"),
                "title": row.get("title"),
                "description": row.get("description"),
            }
            for row in series_topics
        ],
        indent=2,
        ensure_ascii=False,
    )

    target_list = json.dumps(
        [
            {
                "id": row.get("id"),
                "part": row.get("part"),
                "title": row.get("title"),
                "description": row.get("description"),
            }
            for row in target_topics
        ],
        indent=2,
        ensure_ascii=False,
    )

    total_parts = _series_total_parts(series_name) or len(series_topics)

    # ------------------------------------------------------------
    # STAGE 1: Generate only the master chronological timeline.
    # ------------------------------------------------------------
    system_prompt = _series_master_system_prompt() + """

MASTER TIMELINE ONLY:
For this call, return only the master chronological story spine.
Do not write full episode scripts in this response.
Do not include an episodes array.

The goal is to create a complete ordered chain of events first, so no event gets cut.
"""

    user_prompt = f"""
Series name:
{series_name}

Total known parts:
{total_parts}

Full series topic list:
{full_series_list}

Locked previous approved scripts:
{locked_context}

Create a master timeline ONLY for these target episodes:
{target_list}

Rules:
- Continue from locked previous scripts if they exist.
- Do not rewrite locked previous episodes.
- Do not skip causal events.
- Assign every event to exactly one target episode using episode_part.
- Every target episode must have enough timeline events to produce 12 distinct scenes.
- If a previous episode ended with Hannibal near Rome but never taking the city, the next target episode must include events explaining why: walls held, no siege machines, exhausted army, no supplies, Carthage sent no help, Rome refused to surrender, Rome attacked Africa, Hannibal was called home.
- The final event of each episode must naturally set up the first event of the next episode.

Return ONLY this JSON structure:

{{
  "series": "{series_name}",
  "master_timeline": [
    {{
      "event_id": "E01",
      "episode_part": {current_part},
      "event": "Specific chronological event that must appear in this episode",
      "why_it_matters": "Short causal explanation"
    }}
  ]
}}
"""

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=8500,
        temperature=0.55,
    )

    payload = _extract_json(raw)
    master_timeline = payload.get("master_timeline", [])
    if not isinstance(master_timeline, list) or not master_timeline:
        raise Exception(f"Series master timeline returned no events: {payload}")

    master_path = os.path.join(OUTPUT_DIR, f"series_{_safe_filename(series_name)}_master_story.json")
    _save_json_file(master_path, payload)
    print(f"   🧭 Saved series master story: {master_path}")

    # ------------------------------------------------------------
    # STAGE 2: Generate each target episode separately from timeline.
    # ------------------------------------------------------------
    topic_by_id = {str(row["id"]): row for row in series_topics}
    saved_by_id: dict[str, dict] = {}

    previous_full_script_by_part: dict[int, dict] = {}
    for locked in locked_previous_scripts:
        try:
            locked_part = int(locked.get("part", 0) or 0)
        except Exception:
            locked_part = 0
        if locked_part > 0:
            full_locked = _load_json_file(_topic_script_path(locked.get("topic_id", "")), default=None)
            if full_locked:
                previous_full_script_by_part[locked_part] = full_locked

    for episode_topic in sorted(target_topics, key=_topic_part):
        episode_topic_id = str(episode_topic.get("id", "")).strip()
        episode_part = _topic_part(episode_topic)

        timeline_events = [
            event for event in master_timeline
            if _topic_part({"part": event.get("episode_part", 0)}) == episode_part
        ]

        if not timeline_events:
            # Defensive fallback: do not skip the episode just because the timeline
            # writer under-assigned events.
            timeline_events = [
                {
                    "event_id": f"P{episode_part}_FALLBACK",
                    "episode_part": episode_part,
                    "event": str(episode_topic.get("description", episode_topic.get("title", ""))),
                    "why_it_matters": "This topic must still be produced as part of the connected series.",
                }
            ]

        previous_script = previous_full_script_by_part.get(episode_part - 1)
        next_topic = _next_episode_topic(episode_topic)

        print(
            f"   📝 Generating episode script from timeline: "
            f"topic {episode_topic_id}, part {episode_part}, events={len(timeline_events)}"
        )

        script_obj = _generate_episode_script_from_timeline(
            episode_topic=episode_topic,
            series_name=series_name,
            total_parts=total_parts,
            master_timeline=master_timeline,
            timeline_events=timeline_events,
            previous_script=previous_script,
            next_topic=next_topic,
        )

        script_obj = _postprocess_validate_repair(
            script_obj,
            episode_topic,
            previous_script=previous_script,
            next_topic=next_topic,
        )

        script_obj["topic_id"] = episode_topic_id
        script_obj["series"] = episode_topic.get("series", "")
        script_obj["part"] = episode_topic.get("part", 0)
        script_obj["timeline_event_ids"] = [event.get("event_id") for event in timeline_events]

        path = _topic_script_path(episode_topic_id)
        _save_json_file(path, script_obj)
        saved_by_id[episode_topic_id] = script_obj
        previous_full_script_by_part[episode_part] = script_obj
        print(f"   ✅ Saved connected script: {path}")

    selected = saved_by_id.get(current_topic_id)
    if selected:
        print(f"✅ Connected series package generated for {series_name}")
        return selected

    existing_current = _load_json_file(_topic_script_path(current_topic_id), default=None)
    if existing_current:
        return existing_current

    raise Exception(f"Series package did not produce script for topic #{current_topic_id}")



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
    ).strip()
