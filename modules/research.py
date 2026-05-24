"""
modules/research.py
===================
Automated series research pipeline.

Uses the OpenAI Responses API with the hosted web_search tool to generate
a comprehensive, source-backed story bible (JSON) for any series.

The generated file is saved to:
    research/<safe_series_name>.json

And is used as the factual source of truth for:
    - master timeline generation
    - per-episode script generation
    - repair prompts
    - standalone generate_script() for series topics
"""

import hashlib
import json
import os
import re
from datetime import datetime

import requests

from config import OPENAI_API_KEY

try:
    from config import RESEARCH_MODEL
except ImportError:
    RESEARCH_MODEL = "gpt-4.1"

try:
    from config import RESEARCH_AUTO_GENERATE
except ImportError:
    RESEARCH_AUTO_GENERATE = True

try:
    from config import RESEARCH_DIR
except ImportError:
    RESEARCH_DIR = "research"


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


# ---------------------------------------------------------------------------
# Filename / path helpers
# ---------------------------------------------------------------------------

def safe_series_filename(series_name: str) -> str:
    """Convert a series name to a safe lowercase underscore-separated filename."""
    if not series_name:
        return "unnamed_series"
    safe = re.sub(r"[^a-zA-Z0-9\s-]", "", series_name)
    safe = re.sub(r"[-\s]+", "_", safe).lower().strip("_")
    return safe or "unnamed_series"


def research_path_for_series(topic: dict) -> str:
    """Return the full path to the research JSON for a given series."""
    topic_id = str(topic.get("id", "0")).strip()
    from config import SERIES_DATA_DIR
    return os.path.join(SERIES_DATA_DIR, f"series_{topic_id}", "research.json")


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_series_research(topic: dict) -> dict | None:
    """Load an existing research file from disk. Returns None if not found."""
    path = research_path_for_series(topic)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"📚 Loaded series research: {path}")
        return data
    except Exception as e:
        print(f"⚠️  Failed to load research file {path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Internal: Responses API caller with web_search tool
# ---------------------------------------------------------------------------

def _extract_response_text(payload: dict) -> str:
    """
    Extract text from an OpenAI Responses API response.
    Mirrors the helper used in modules/script.py and modules/images.py.
    """
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
        raise Exception(f"OpenAI Responses API returned no readable text: {payload}")
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


def _call_with_tool(
    system_prompt: str,
    user_prompt: str,
    tool_type: str,
    reasoning_effort: str | None = None,
    max_output_tokens: int = 7000,
) -> str:
    """
    Call the OpenAI Responses API with a single tool definition.
    Handles GPT-5 family models (adding reasoning_effort).
    """
    if not str(OPENAI_API_KEY or "").strip():
        raise Exception(
            "OPENAI_API_KEY is not set. Set it in config.py to enable automated research."
        )

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Detect GPT-5 family
    is_gpt5 = "gpt-5" in RESEARCH_MODEL.lower() or "5.5" in RESEARCH_MODEL.lower()

    payload = {
        "model": RESEARCH_MODEL,
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
        "tools": [{"type": tool_type}],
        "max_output_tokens": max_output_tokens,
    }

    if is_gpt5 and reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}

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
            f"OpenAI Responses API error {response.status_code} "
            f"(tool={tool_type}): {err_msg}"
        )

    return _extract_response_text(response.json())


def _openai_text_response(
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 4000,
    reasoning_effort: str | None = None,
) -> str:
    """
    Calls OpenAI Responses API for text generation (no tools).
    Used for JSON repair.
    """
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    is_gpt5 = "gpt-5" in RESEARCH_MODEL.lower() or "5.5" in RESEARCH_MODEL.lower()

    payload = {
        "model": RESEARCH_MODEL,
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

    if is_gpt5 and reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    elif not is_gpt5:
        payload["temperature"] = 0.3

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
        raise Exception(f"OpenAI text error {response.status_code}: {err_msg}")

    return _extract_response_text(response.json())


def _call_openai_with_tool(
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None = None,
    max_output_tokens: int = 7000,
) -> str:
    """
    Try web_search first. If the tool type is unsupported, retry once
    with web_search_preview. If both fail, raise — do not invent research.
    """
    first_error: Exception | None = None

    try:
        return _call_with_tool(
            system_prompt, user_prompt, "web_search", 
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens
        )
    except Exception as e:
        first_error = e
        err_lower = str(e).lower()
        # Only retry on an unsupported/unknown tool error, not on auth or quota errors.
        if any(k in err_lower for k in ("unsupported", "unknown", "invalid", "web_search")):
            print(
                "⚠️  'web_search' tool not supported by this model. "
                "Retrying with 'web_search_preview'..."
            )
        else:
            raise

    try:
        return _call_with_tool(
            system_prompt, user_prompt, "web_search_preview", 
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens
        )
    except Exception as e2:
        raise Exception(
            "Automated research failed with both 'web_search' and 'web_search_preview'.\n"
            f"  First error:  {first_error}\n"
            f"  Second error: {e2}\n"
            "Do not proceed without verified research. Fix your API access or tool support."
        ) from e2


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(raw: str, prefix: str = "research") -> dict:
    """
    Extract JSON from a model response even if wrapped in code fences.
    More robust than script.py: strips markdown, extracts from { to },
    removes trailing commas, and saves debug output on failure.
    """
    text = raw.strip()

    # 1. Strip markdown fences
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        text = text.strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    # 2. Try raw parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Extract from first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    # 4. Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 5. Try parse again
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Save invalid JSON for debugging
        from config import TEMP_DIR
        os.makedirs(TEMP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_path = os.path.join(TEMP_DIR, f"invalid_{prefix}_json_{timestamp}.txt")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(raw)
        
        raise Exception(
            f"Failed to parse {prefix} JSON. Error: {e}\n"
            f"Invalid response saved to: {debug_path}"
        ) from e


# ---------------------------------------------------------------------------
# Research generation
# ---------------------------------------------------------------------------

def generate_series_research(topic: dict) -> dict:
    """
    Generate a source-backed research bible for a series using the
    OpenAI Responses API with the hosted web_search tool.

    Saves the result to research/<safe_series_name>.json and returns it.
    Raises if the API call fails — never silently invents research.
    """
    series_name = str(topic.get("title", "")).strip()
    print(f"\n🔍 Generating automated web-search research for: \"{series_name}\"...")

    system_prompt = (
        "You are a meticulous historical researcher building a comprehensive story bible "
        "for a cinematic history short-form video series.\n\n"
        "You have access to the web_search tool. You MUST use it to gather accurate dates, "
        "locations, key people, causes, and effects. Do NOT invent source names or URLs.\n"
        "If you use a source, include its real name and real URL in source_notes.\n"
        "If you are unsure about a fact, mark confidence as 'medium' or 'low'.\n\n"
        "STORY DENSITY REQUIREMENT:\n"
        "The research must be dense enough to support a full multi-part series (up to 10 episodes).\n"
        "Aim for a timeline of 30–50 specific events/beats.\n"
        "CONCISENESS RULES:\n"
        "- Keep each event concise. Use short fields, not paragraphs.\n"
        "- Do not write long explanations inside every field.\n"
        "- Source notes should be brief.\n\n"
        "Output ONLY strictly valid JSON matching the schema requested. "
        "No markdown, no explanations outside the JSON."
    )

    today = datetime.utcnow().strftime("%Y-%m-%d")
    user_prompt = f"""
Use your web_search tool to research the following series and produce a complete research bible.

Series: "{series_name}"
Series Description: "{topic.get("description", "")}"
Max Episodes planned: {topic.get("max_episodes", 10)}

Today's date: {today}

The research MUST produce a DENSE timeline (30–50 events).
Do not skip the "boring" parts that explain WHY things happened.
Keep descriptions compact.

For every event, provide:
- event_id (unique string)
- date (e.g. "Spring 216 BCE")
- event (compact description)
- cause (why it happened)
- effect (what it caused next)
- people (who was there)
- place (location + country)
- confidence (high/medium/low)
- source_note_id (link to source_notes)

{"" if "carthage" not in series_name.lower() else """
Because this is "The Fall of Carthage", you MUST specifically resolve these bridges:
- Explain why Hannibal did NOT take Rome after Cannae (lack of siege machines, exhaustion, Rome's walls).
- Explain Scipio Africanus's rise separately from his later descendants.
- Explain the Battle of Zama: It was in North Africa (Tunisia), and Hannibal SURVIVED.
- Explain the 201 BCE Peace Terms: fleet burned, indemnity, military restricted.
- Explain the 50-year gap: Carthage's economic recovery.
- Explain Cato's "Carthago delenda est".
- Explain Scipio Aemilianus as distinct from Scipio Africanus.
- Do NOT imply Rome salted the earth unless as a potential myth.
"""}

Separate your output into:
1. verified_facts
2. debated_or_uncertain_facts
3. myths_or_must_not_imply

Return ONLY this exact JSON structure:

{{
  "research_schema_version": "1.1",
  "series": "{series_name}",
  "research_generated_by": "OpenAI Responses API web_search",
  "research_last_updated": "{today}",
  "source_quality_note": "Summary of research coverage.",
  "verified_facts": [],
  "debated_or_uncertain_facts": [],
  "myths_or_must_not_imply": [],
  "historical_timeline": [
    {{
      "event_id": "EV001",
      "date": "...",
      "event": "...",
      "cause": "...",
      "effect": "...",
      "people": ["..."],
      "place": "...",
      "confidence": "high",
      "source_note_id": "SN1"
    }}
  ],
  "source_notes": [
    {{
      "id": "SN1",
      "source_name": "...",
      "url": "...",
      "note": "..."
    }}
  ]
}}
"""

    raw = _call_openai_with_tool(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        reasoning_effort="high",
        max_output_tokens=14000
    )

    if not raw or not raw.strip():
        raise Exception(
            "The research model returned an empty response. "
            "Cannot proceed without verified research."
        )

    try:
        research_data = _extract_json(raw, prefix="research")
    except Exception as e:
        print(f"⚠️  Research JSON is malformed. Attempting one-time repair... (Error: {e})")
        
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
                max_output_tokens=14000,
                reasoning_effort="low"
            )
            research_data = _extract_json(repaired_raw, prefix="research_repaired")
            print("✅ Research JSON repaired successfully.")
        except Exception as e2:
            raise Exception(
                f"Failed to generate or repair research JSON.\n"
                f"  Primary error: {e}\n"
                f"  Repair error:  {e2}"
            ) from e2

    # Save to disk
    path = research_path_for_series(topic)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(research_data, f, indent=2, ensure_ascii=False)

    print(f"✅ Research bible saved: {path}")
    return research_data


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ensure_series_research(
    topic: dict,
    rebuild: bool = False,
) -> dict | None:
    """
    Ensure research exists for a series.

    - If rebuild=True, always regenerate (even if file exists).
    - If rebuild=False and a file exists, load and return it.
    - If rebuild=False and no file exists, generate it (unless RESEARCH_AUTO_GENERATE=False).

    Returns the research dict, or None if auto-generate is disabled and no file exists.
    """
    if not rebuild:
        existing = load_series_research(topic)
        if existing:
            return existing

    if not RESEARCH_AUTO_GENERATE and not rebuild:
        print(
            f"ℹ️  No research file found for '{series_name}' and "
            "RESEARCH_AUTO_GENERATE=False. Skipping research."
        )
        return None

    return generate_series_research(topic)


# ---------------------------------------------------------------------------
# Hash utility
# ---------------------------------------------------------------------------

def get_research_hash(research_data: dict | None) -> str | None:
    """
    Compute a short SHA-256 hash of the research dict.
    Used to detect whether research changed between runs.
    Returns None if research_data is None.
    """
    if not research_data:
        return None
    raw = json.dumps(research_data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
