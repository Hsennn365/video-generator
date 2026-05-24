import json
import os
import re

import requests

from config import ELEVENLABS_API_KEY, TEMP_DIR

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"

SCRIBE_KEYTERMS = [
    "Carthage",
    "Rome",
    "Hannibal",
    "empire",
    "civilization",
]


STRONG_END_RE = re.compile(r"[.!?]+\s*$")


# ============================================
# Caption Timing Settings
# ============================================
# Word-sync mode:
# - A caption should appear only when that word is spoken.
# - This avoids showing a whole phrase/scene before the voice reaches it.
_CAPTION_GAP_S = 0.025

# Tiny minimum so FFmpeg/ASS has enough time to render the word.
_MIN_WORD_DURATION_S = 0.08

# Normal words should disappear quickly instead of staying across many words.
_MAX_WORD_DURATION_S = 0.70

# Words ending sentences can stay slightly longer for dramatic rhythm.
_MAX_STRONG_END_DURATION_S = 1.05

# Small hold after the word ends, as long as it does not overlap the next word.
_NORMAL_HOLD_AFTER_WORD_S = 0.10
_STRONG_HOLD_AFTER_WORD_S = 0.35


def _extract_transcript_chunk(payload: dict) -> dict:
    """Resolve sync STT JSON to a single transcript chunk."""
    if "transcripts" in payload:
        transcripts = payload["transcripts"]
        if not transcripts:
            raise Exception("ElevenLabs STT error: empty transcripts")
        return transcripts[0]

    if "words" in payload:
        return payload

    if "message" in payload and "request_id" in payload:
        raise Exception(
            "ElevenLabs STT returned an async webhook payload; ensure webhook is disabled."
        )

    raise Exception(
        f"ElevenLabs STT error: unexpected response keys: {list(payload.keys())}"
    )


def _normalize_words_to_ms_format(eleven_words: list) -> list:
    """
    ElevenLabs returns start/end in seconds.
    Downstream expects milliseconds.
    Keeps only real spoken words.
    """
    out = []

    for w in eleven_words:
        if w.get("type") != "word":
            continue

        text = (w.get("text") or "").strip()
        if not text:
            continue

        start = w.get("start")
        end = w.get("end")

        if start is None or end is None:
            continue

        out.append(
            {
                "text": text,
                "start": round(float(start) * 1000.0),
                "end": round(float(end) * 1000.0),
            }
        )

    return out


def generate_captions(audio_path: str, prefix: str) -> list:
    """
    Transcribes audio with ElevenLabs Scribe and returns word-level timestamps.
    Word objects use start/end in milliseconds.
    """
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    print("  📤 Sending audio to ElevenLabs Scribe…")

    data = [
        ("model_id", "scribe_v2"),
        ("timestamps_granularity", "word"),
        ("webhook", "false"),
    ]

    for term in SCRIBE_KEYTERMS:
        data.append(("keyterms", term))

    with open(audio_path, "rb") as audio_file:
        files = {
            "file": (
                os.path.basename(audio_path),
                audio_file,
                "application/octet-stream",
            ),
        }

        response = requests.post(
            ELEVENLABS_STT_URL,
            headers=headers,
            files=files,
            data=data,
            timeout=600,
        )

    if response.status_code != 200:
        raise Exception(f"ElevenLabs STT error {response.status_code}: {response.text}")

    payload = response.json()
    chunk = _extract_transcript_chunk(payload)
    raw_words = chunk.get("words", [])
    words = _normalize_words_to_ms_format(raw_words)

    print(f"  ✅ Captions generated: {len(words)} words")

    captions_path = os.path.join(TEMP_DIR, f"{prefix}captions.json")
    with open(captions_path, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2, ensure_ascii=False)

    return words


def _clean_caption_word(text: str) -> str:
    """
    Keep punctuation because it helps dramatic words like:
    CARTHAGE.
    ROME.
    """
    return str(text or "").strip().replace("\n", " ")


def _is_strong_end(text: str) -> bool:
    return bool(STRONG_END_RE.search(str(text or "")))


def _word_to_caption(
    word: dict,
    next_word: dict | None,
) -> dict | None:
    """
    Convert one spoken word into one displayed caption.

    The caption starts exactly when this word starts.
    It ends before the next word starts, so it never shows future words early.
    """
    text = _clean_caption_word(word.get("text", ""))

    if not text:
        return None

    start_ms = int(word.get("start", 0) or 0)
    end_ms = int(word.get("end", start_ms) or start_ms)

    start_sec = round(start_ms / 1000.0, 3)
    word_end_sec = round(end_ms / 1000.0, 3)

    strong_end = _is_strong_end(text)

    hold = _STRONG_HOLD_AFTER_WORD_S if strong_end else _NORMAL_HOLD_AFTER_WORD_S
    max_duration = _MAX_STRONG_END_DURATION_S if strong_end else _MAX_WORD_DURATION_S

    desired_end = word_end_sec + hold
    desired_end = min(desired_end, start_sec + max_duration)

    if next_word is not None:
        next_start_sec = round(int(next_word.get("start", end_ms) or end_ms) / 1000.0, 3)
        desired_end = min(desired_end, next_start_sec - _CAPTION_GAP_S)

    # Keep caption duration positive, but never force it to overlap the next word.
    if desired_end <= start_sec:
        desired_end = start_sec + _MIN_WORD_DURATION_S

        if next_word is not None:
            next_start_sec = round(int(next_word.get("start", end_ms) or end_ms) / 1000.0, 3)
            desired_end = min(desired_end, next_start_sec - _CAPTION_GAP_S)

    # If words are extremely close, still create a tiny safe caption.
    if desired_end <= start_sec:
        desired_end = start_sec + 0.04

    duration = round(desired_end - start_sec, 3)

    return {
        "text": text.upper(),
        "start": start_sec,
        "end": round(desired_end, 3),
        "duration": duration,
    }


def _remove_caption_overlaps(captions: list, gap: float = _CAPTION_GAP_S) -> list:
    """
    Defensive cleanup: one caption must end before the next starts.
    """
    if not captions:
        return []

    cleaned = []

    for cap in captions:
        if not cap.get("text"):
            continue

        cleaned.append(dict(cap))

    for i in range(len(cleaned) - 1):
        cur = cleaned[i]
        nxt = cleaned[i + 1]

        max_end = round(float(nxt["start"]) - gap, 3)

        if float(cur["end"]) > max_end:
            cur["end"] = max_end
            cur["duration"] = round(max(0.04, max_end - float(cur["start"])), 3)

    return cleaned


def format_captions_for_creatomate(
    words: list,
    max_words: int | None = None,
    max_duration: float | None = None,
    min_duration: float | None = None,
) -> list:
    """
    Word-synced captions.

    This intentionally does NOT group full phrases, because grouped phrases appear
    before every word in the phrase is spoken.

    Output format remains compatible with local assembly.py:
    {
      text,
      start,
      end,
      duration
    }
    """
    _ = max_words, max_duration, min_duration

    if not words:
        return []

    rows = []

    for w in words:
        text = _clean_caption_word(w.get("text", ""))

        if not text:
            continue

        start_ms = int(w.get("start", 0) or 0)
        end_ms = int(w.get("end", start_ms) or start_ms)

        rows.append(
            {
                "text": text,
                "start": start_ms,
                "end": end_ms,
            }
        )

    captions = []

    for i, word in enumerate(rows):
        next_word = rows[i + 1] if i + 1 < len(rows) else None
        cap = _word_to_caption(word, next_word)

        if cap:
            captions.append(cap)

    captions = _remove_caption_overlaps(captions)

    print(f"   📝 Word-synced captions: {len(captions)} words")
    return captions