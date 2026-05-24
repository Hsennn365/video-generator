"""
modules/social_captions.py
===========================
Generate platform social captions from an existing script JSON dict.
No files are written — everything is returned in-memory.

The script JSON is expected to contain at minimum:
  - title          (str)
  - hook           (str)
  - full_narration (str)

Optional but used when present:
  - youtube_description (str)
  - hashtags            (list[str] | str)
  - series              (str)
  - part / episode_number (int | str)
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean(text: Any) -> str:
    return str(text or "").strip()


def _hashtag_string(hashtags: Any) -> str:
    """Return a space-separated hashtag string from list or string."""
    if isinstance(hashtags, list):
        tags = [h.strip().lstrip("#") for h in hashtags if h and str(h).strip()]
        return " ".join(f"#{t}" for t in tags if t)
    if isinstance(hashtags, str):
        text = hashtags.strip()
        # Already formatted as #tag #tag …
        if text.startswith("#"):
            return text
        # Comma or space separated words without #
        parts = re.split(r"[,\s]+", text)
        return " ".join(f"#{p.strip()}" for p in parts if p.strip())
    return ""


def _first_n_sentences(text: str, n: int = 2) -> str:
    """Return the first n sentences from text."""
    sentences = re.findall(r"[^.!?]+[.!?]?", text)
    chosen = [s.strip() for s in sentences[:n] if s.strip()]
    return " ".join(chosen)


def _build_default_hashtags(script: dict) -> str:
    """Build sensible default hashtags when none exist in the script."""
    tags = ["history", "darkhistory", "ancienthistory", "shorts", "historyfacts"]
    series = _clean(script.get("series"))
    if series:
        slug = re.sub(r"[^a-zA-Z0-9]+", "", series.title())
        if slug:
            tags.insert(0, slug)
    title_words = re.findall(r"[a-zA-Z]{4,}", _clean(script.get("title", "")))
    for w in title_words[:2]:
        if w.lower() not in {"with", "from", "that", "this", "will", "were", "have"}:
            tags.append(w.lower())
    return " ".join(f"#{t}" for t in dict.fromkeys(tags))  # deduplicate, preserve order


def _episode_label(script: dict) -> str:
    ep = script.get("part") or script.get("episode_number")
    series = _clean(script.get("series"))
    if ep and series:
        return f"Part {ep}"
    if ep:
        return f"Part {ep}"
    return ""


def merge_topic_context(script: dict, topic: dict | None = None) -> dict:
    """
    Fill missing script fields from the topics.csv row (new + legacy columns).
    Does not assume instagram_caption / tiktok_caption exist on the script.
    """
    merged = dict(script or {})
    topic = topic or {}

    if not _clean(merged.get("title")):
        merged["title"] = _clean(topic.get("title"))

    series = (
        _clean(merged.get("series"))
        or _clean(topic.get("series"))
        or _clean(topic.get("series_slug"))
    )
    if series:
        merged["series"] = series

    ep = (
        merged.get("part")
        or merged.get("episode_number")
        or topic.get("part")
        or topic.get("episode")
    )
    if ep not in (None, "", 0, "0"):
        merged["part"] = ep

    if not _clean(merged.get("hook")) and _clean(merged.get("full_narration")):
        merged["hook"] = _first_n_sentences(_clean(merged["full_narration"]), n=1)

    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_social_captions(script: dict, topic: dict | None = None) -> dict:
    """
    Build social media captions from existing script fields.

    Returns a dict with keys:
        instagram_caption
        tiktok_caption
        youtube_title
        youtube_description
        hashtags
    Nothing is written to disk.
    """
    script = merge_topic_context(script, topic)
    title = _clean(script.get("title"))
    hook = _clean(script.get("hook"))
    narration = _clean(script.get("full_narration"))
    ep_label = _episode_label(script)
    series = _clean(script.get("series"))

    # ── Hashtags ────────────────────────────────────────────────────────────
    raw_hashtags = script.get("hashtags")
    if raw_hashtags:
        hashtag_str = _hashtag_string(raw_hashtags)
    else:
        hashtag_str = _build_default_hashtags(script)

    # ── YouTube Title ───────────────────────────────────────────────────────
    if ep_label and series:
        yt_title = f"{series} — {title} ({ep_label}) #Shorts"
    elif ep_label:
        yt_title = f"{title} ({ep_label}) #Shorts"
    else:
        yt_title = f"{title} #Shorts"
    yt_title = yt_title[:100]  # YouTube hard limit

    # ── YouTube Description ─────────────────────────────────────────────────
    existing_desc = _clean(script.get("youtube_description"))
    if existing_desc:
        # Reuse but ensure hashtags are appended
        if "#" not in existing_desc:
            yt_description = f"{existing_desc}\n\n{hashtag_str}"
        else:
            yt_description = existing_desc
    else:
        # Build from narration intro + hook
        summary = _first_n_sentences(narration, n=3) if narration else hook
        cta = "Follow for more dark history." if not series else f"Follow for the next part of {series}."
        yt_description = f"{hook}\n\n{summary}\n\n{cta}\n\n{hashtag_str}"
    yt_description = yt_description[:5000]  # YouTube limit

    # ── Instagram Caption ───────────────────────────────────────────────────
    ig_hook = hook or _first_n_sentences(narration, n=1)
    if ep_label and series:
        ig_intro = f"{series} | {ep_label}\n\n"
    elif ep_label:
        ig_intro = f"{ep_label}\n\n"
    else:
        ig_intro = ""
    cta_ig = "Follow for more 🏛️" if not series else f"Follow for the next part 👇"
    instagram_caption = f"{ig_intro}{title}\n\n{ig_hook}\n\n{cta_ig}\n\n{hashtag_str}"
    instagram_caption = instagram_caption[:2200]  # Instagram limit

    # ── TikTok Caption ──────────────────────────────────────────────────────
    tt_hook = hook or _first_n_sentences(narration, n=1)
    if ep_label:
        tiktok_caption = f"{ep_label}: {title}\n{tt_hook}\n\n{hashtag_str}"
    else:
        tiktok_caption = f"{title}\n{tt_hook}\n\n{hashtag_str}"
    tiktok_caption = tiktok_caption[:2200]  # TikTok limit

    return {
        "instagram_caption": instagram_caption,
        "tiktok_caption": tiktok_caption,
        "youtube_title": yt_title,
        "youtube_description": yt_description,
        "hashtags": hashtag_str,
    }


def print_social_captions(script: dict, topic: dict | None = None, caps: dict | None = None) -> dict:
    """
    Generate captions from script and print them to the terminal.
    Returns the captions dict (nothing is saved to disk).
    """
    caps = caps or extract_social_captions(script, topic=topic)

    print("\n" + "=" * 60)
    print("📢  SOCIAL CAPTIONS")
    print("=" * 60)

    print("\n── YouTube Title ──────────────────────────────────────────")
    print(caps["youtube_title"])

    print("\n── YouTube Description ────────────────────────────────────")
    print(caps["youtube_description"])

    print("\n── Instagram Caption ──────────────────────────────────────")
    print(caps["instagram_caption"])

    print("\n── TikTok Caption ─────────────────────────────────────────")
    print(caps["tiktok_caption"])

    print("\n── Hashtags ───────────────────────────────────────────────")
    print(caps["hashtags"])

    print("\n" + "=" * 60 + "\n")

    return caps
