"""
ASH & EMPIRES — Video Generation Pipeline
==========================================
Main orchestrator. Run this to generate and post a video.

Usage:
  python main.py                                  # Process next pending topic
  python main.py --topic-id 3                     # Process specific topic
  python main.py --preview                        # Generate but don't post
  python main.py --topic-id 1 --rebuild-assembly  # Re-render only
  python main.py --topic-id 1 --rebuild-audio --rebuild-captions --rebuild-assembly
  python main.py --topic-id 1 --rebuild-all       # Fresh generation
  python main.py --list                           # List all topics and status
  python main.py --analytics                      # Update performance data
  python main.py --voices                         # List available ElevenLabs voices
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.script import (
    generate_script,
    generate_series_script_package,
    generate_thumbnail_prompt,
    get_asset_prefix,
    get_script_path,
    get_series_package_path,
    _is_series_topic,
)
from modules.research import (
    ensure_series_research,
    research_path_for_series,
    load_series_research,
)
from modules.voiceover import generate_voiceover, get_available_voices
from modules.images import generate_image, generate_thumbnail_image, prepare_scene_image_prompts
from modules.captions import generate_captions, format_captions_for_creatomate
from modules.assembly import assemble_video
from modules.publisher import publish_all
from modules.tracker import init_logs, log_video, update_performance, print_performance_summary
from config import TOPICS_FILE, TEMP_DIR, OUTPUT_DIR

try:
    from config import GENERATE_THUMBNAIL
except ImportError:
    GENERATE_THUMBNAIL = True


# -----------------------------
# Helpers
# -----------------------------

def load_topics() -> list:
    """Load all topics from CSV."""
    if not os.path.exists(TOPICS_FILE):
        raise FileNotFoundError(f"Topics file not found: {TOPICS_FILE}")

    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def get_next_topic(topics: list) -> dict | None:
    """Get the next pending topic."""
    for topic in topics:
        if topic.get("status") == "pending":
            return topic
    return None


def get_topic_by_id(topics: list, topic_id: str) -> dict | None:
    """Get a specific topic by ID."""
    for topic in topics:
        if topic.get("id") == str(topic_id):
            return topic
    return None


def mark_topic_done(topic_id: str):
    """Mark a topic as completed in the CSV."""
    topics = load_topics()

    for topic in topics:
        if topic.get("id") == str(topic_id):
            topic["status"] = "done"

    with open(TOPICS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=topics[0].keys())
        writer.writeheader()
        writer.writerows(topics)


def list_topics():
    """Print all topics with their status."""
    topics = load_topics()
    print("\n📋 ALL TOPICS:")
    print("-" * 70)
    for t in topics:
        series_info = (
            f"[{t.get('series')} P{t.get('part')}]"
            if t.get("series") and t.get("part") != "0"
            else "[standalone]"
        )
        status_icon = "✅" if t.get("status") == "done" else "⏳"
        print(f"  {status_icon} #{t.get('id')} {series_info} {t.get('title')}")
    print()


def cleanup_temp():
    """Clean up temporary files after successful pipeline run."""
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR, exist_ok=True)
    print("🧹 Temp files cleaned up")


def _delete_file(path: str | Path):
    """Delete a file if it exists."""
    path = Path(path)
    if path.is_file():
        path.unlink()
        print(f"🗑️  Deleted cache: {path}")


def _delete_glob(pattern: str):
    """Delete every file matching a glob pattern."""
    for path in Path(".").glob(pattern):
        if path.is_file():
            path.unlink()
            print(f"🗑️  Deleted cache: {path}")


def _hash_text(text: str) -> str:
    """Hash narration so old audio/captions cannot be reused with a changed script."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]




def _word_tokens(text: str) -> list[str]:
    """Very small tokenizer for cache sanity checks."""
    import re
    return re.findall(r"[a-z0-9]+", text.lower())


# Common English stopwords that show up in almost any narration and therefore
# cannot prove that two scripts are the same. Keeping this list short and
# deliberate so the ordered comparator below stays meaningful.
_CAPTION_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "as", "is", "was", "were", "be", "been", "being",
    "are", "am", "it", "its", "this", "that", "these", "those", "i", "you",
    "he", "she", "we", "they", "them", "his", "her", "their", "my", "your",
    "our", "s", "t", "ll", "ve", "re", "d", "m", "if", "then", "than", "so",
    "do", "does", "did", "not", "no", "into", "over", "under", "up", "out",
    "had", "has", "have", "will", "would", "could", "should",
}


def _content_tokens(text: str, limit: int = 8) -> list[str]:
    """Ordered list of meaningful (non-stopword) tokens from the start of text."""
    out: list[str] = []
    for token in _word_tokens(text):
        if token in _CAPTION_STOPWORDS:
            continue
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _captions_match_narration_ordered(
    words: list,
    narration: str,
    *,
    compare_n: int = 8,
    min_match: int = 5,
) -> bool:
    """
    Strong caption/narration check: compare the first compare_n meaningful words
    of captions and narration *in order*. Stale captions from a different script
    will diverge in the first few content words even when the topic is the same,
    so this catches mismatches that simple word-overlap silently approves.
    """
    if not words or not narration:
        return False

    caption_text = " ".join(str(w.get("text", "")) for w in words[:60])
    caption_seq = _content_tokens(caption_text, limit=compare_n)
    narration_seq = _content_tokens(narration, limit=compare_n)

    if len(caption_seq) < min_match or len(narration_seq) < min_match:
        return False

    matches = sum(1 for a, b in zip(caption_seq, narration_seq) if a == b)
    return matches >= min_match


def _file_mtime(path: str) -> float:
    """Return file modification time, or 0.0 if the file does not exist."""
    return os.path.getmtime(path) if os.path.isfile(path) else 0.0


def _load_json(path: str | Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.is_file():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str | Path, data: Any):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _duration_from_words(words: list, fallback_seconds: float = 90.0) -> float:
    """
    Use the final word timestamp as the true video length.
    Word timestamps are stored in milliseconds.
    """
    if not words:
        return fallback_seconds

    last_end_ms = max((w.get("end") or 0) for w in words)
    if last_end_ms <= 0:
        return fallback_seconds

    # Add a tiny outro buffer so the last spoken word is not clipped.
    return round((last_end_ms / 1000.0) + 0.6, 2)



def _retime_images_evenly(image_paths: list, video_duration: float) -> list:
    """
    Last-resort timing only. Prefer caption-word alignment.
    """
    if not image_paths:
        return []

    scene_duration = video_duration / len(image_paths)
    retimed = []

    for i, img in enumerate(sorted(image_paths, key=lambda x: int(x["scene_number"]))):
        start = round(i * scene_duration, 3)
        end = round(video_duration if i == len(image_paths) - 1 else (i + 1) * scene_duration, 3)
        updated = dict(img)
        updated["timestamp_start"] = start
        updated["timestamp_end"] = end
        updated["_timing_source"] = "even_fallback"
        updated["_use_pipeline_timing"] = True
        retimed.append(updated)

    return retimed


def _content_token_stream_from_words(words: list) -> list[dict]:
    """
    Convert word-level STT output into an ordered stream of meaningful tokens
    while keeping the original word timestamps.
    """
    stream: list[dict] = []

    for w in words:
        raw = str(w.get("text", "")).strip()
        if not raw:
            continue

        start_ms = int(w.get("start", 0) or 0)
        end_ms = int(w.get("end", start_ms) or start_ms)

        for tok in _word_tokens(raw):
            if tok in _CAPTION_STOPWORDS:
                continue
            stream.append(
                {
                    "tok": tok,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "raw": raw,
                }
            )

    return stream


def _content_tokens_all(text: str) -> list[str]:
    """All meaningful ordered tokens from a text string."""
    return [tok for tok in _word_tokens(text) if tok not in _CAPTION_STOPWORDS]


def _find_content_pattern(stream: list[dict], cursor: int, pattern: list[str]) -> int | None:
    """Find an exact consecutive content-token pattern in the STT stream."""
    if not pattern:
        return None

    length = len(pattern)
    if length <= 0 or cursor > len(stream) - length:
        return None

    for start_idx in range(cursor, len(stream) - length + 1):
        if all(stream[start_idx + k]["tok"] == pattern[k] for k in range(length)):
            return start_idx

    return None


def _find_scene_start_in_stream(
    stream: list[dict],
    cursor: int,
    narration_segment: str,
) -> tuple[int | None, int]:
    """
    Find where a scene starts in the real caption/audio stream.

    Uses meaningful tokens, not raw full text, because STT may split contractions,
    remove punctuation, or normalize small words.
    """
    tokens = _content_tokens_all(narration_segment)
    if not tokens:
        return None, 0

    # Try stronger patterns first, then smaller ones.
    # Short scene narration is intentional, so 2 meaningful words can be enough
    # when we are searching sequentially after the previous scene.
    max_len = min(7, len(tokens))
    min_len = 2 if len(tokens) >= 2 else 1

    for length in range(max_len, min_len - 1, -1):
        idx = _find_content_pattern(stream, cursor, tokens[:length])
        if idx is not None:
            return idx, length

    return None, 0


def _interpolate_missing_starts(
    starts: list[float | None],
    video_duration_sec: float,
) -> list[float]:
    """
    Fill unmatched scene starts without throwing away every successful match.
    This prevents one failed scene from causing full proportional fallback.
    """
    n = len(starts)
    if n == 0:
        return []

    filled: list[float | None] = starts[:]

    # Force first scene to start at 0 if it was not matched.
    if filled[0] is None:
        filled[0] = 0.0

    # If every later scene failed, evenly distribute after scene 1.
    known = [i for i, v in enumerate(filled) if v is not None]
    if len(known) == 1:
        return [round((i / n) * float(video_duration_sec), 3) for i in range(n)]

    # Interpolate gaps between known points.
    known = [i for i, v in enumerate(filled) if v is not None]
    for left_i, right_i in zip(known, known[1:]):
        left_t = float(filled[left_i])
        right_t = float(filled[right_i])
        gap_count = right_i - left_i
        if gap_count <= 1:
            continue

        step = (right_t - left_t) / gap_count
        for j in range(left_i + 1, right_i):
            filled[j] = round(left_t + step * (j - left_i), 3)

    # Fill anything after the last known scene.
    known = [i for i, v in enumerate(filled) if v is not None]
    last_known = known[-1]
    last_t = float(filled[last_known])
    remaining = n - last_known - 1
    if remaining > 0:
        available = max(0.5, float(video_duration_sec) - last_t)
        step = available / (remaining + 1)
        for j in range(last_known + 1, n):
            filled[j] = round(last_t + step * (j - last_known), 3)

    # Enforce monotonic starts.
    result: list[float] = []
    prev = 0.0
    for i, v in enumerate(filled):
        val = float(v if v is not None else (i / n) * float(video_duration_sec))
        if i == 0:
            val = 0.0
        elif val <= prev:
            val = prev + 0.25
        result.append(round(min(val, float(video_duration_sec)), 3))
        prev = result[-1]

    return result


def _align_image_paths_to_words(
    words: list,
    script: dict,
    image_paths: list,
    video_duration_sec: float,
) -> list:
    """
    Align each scene image to the real voiceover timing.

    Scene start = first matched meaningful word from that scene's narration.
    Scene end   = next scene start, or the final caption word for the last scene.

    Unlike the previous version, one failed match does NOT make the whole video
    fall back to proportional timing. Missing scenes are interpolated between
    the nearest successful matches and are logged clearly.
    """
    images = sorted(image_paths, key=lambda x: int(x["scene_number"]))
    scenes = script.get("scenes") or []

    scene_by_num: dict[int, dict] = {}
    for scene in scenes:
        try:
            scene_by_num[int(scene["scene_number"])] = scene
        except (TypeError, KeyError, ValueError):
            continue

    if not words or not images:
        print("   ⚠️  No caption words/images available — using even image timing fallback.")
        return _retime_images_evenly(images, video_duration_sec)

    stream = _content_token_stream_from_words(words)
    if not stream:
        print("   ⚠️  Caption token stream is empty — using even image timing fallback.")
        return _retime_images_evenly(images, video_duration_sec)

    cursor = 0
    matched_indices: list[int | None] = []
    failed_scenes: list[int] = []

    for img in images:
        scene_num = int(img["scene_number"])
        scene = scene_by_num.get(scene_num, {})
        segment = scene.get("narration_segment") or scene.get("narration") or ""

        idx, matched_len = _find_scene_start_in_stream(stream, cursor, segment)
        if idx is None:
            matched_indices.append(None)
            failed_scenes.append(scene_num)
            continue

        matched_indices.append(idx)
        cursor = idx + max(matched_len, 1)

    last_word_end_sec = round(max(int(w.get("end", 0) or 0) for w in words) / 1000.0, 3)
    final_end = max(float(video_duration_sec), last_word_end_sec)

    raw_starts: list[float | None] = [
        None if idx is None else round(stream[idx]["start_ms"] / 1000.0, 3)
        for idx in matched_indices
    ]

    starts = _interpolate_missing_starts(raw_starts, final_end)

    if failed_scenes:
        print(f"   ⚠️  Scene timing matched with interpolation for scenes: {failed_scenes}")
    else:
        print("   ✅ Every scene image matched directly to caption word timestamps.")

    out = []
    gap = 0.03

    print("   🕒 Scene timing report:")
    for i, img in enumerate(images):
        start = starts[i]
        if i + 1 < len(starts):
            end = max(start + 0.25, starts[i + 1] - gap)
        else:
            end = final_end

        end = round(min(end, final_end), 3)
        start = round(max(0.0, start), 3)

        updated = dict(img)
        updated["timestamp_start"] = start
        updated["timestamp_end"] = end
        updated["_use_pipeline_timing"] = True
        updated["_timing_source"] = "caption_words" if matched_indices[i] is not None else "interpolated"

        print(
            f"      Scene {int(img['scene_number']):02d}: "
            f"{start:>6.2f}s → {end:>6.2f}s "
            f"({updated['_timing_source']})"
        )

        out.append(updated)

    return out


def _merge_short_image_intervals(image_paths: list, min_duration: float = 3.0) -> list:
    """
    Prevent fast image flashes.

    Captions remain synced to audio. This only changes how long each image stays
    on screen. If a visual beat is shorter than min_duration, we merge it into
    the previous image interval. If the first image is too short, merge it into
    the next image interval.

    This means one image may cover multiple caption phrases, which is better
    than flashing a new image for 1–2 seconds.
    """
    if not image_paths:
        return []

    rows = sorted(image_paths, key=lambda x: float(x.get("timestamp_start", 0)))
    smoothed = []
    merged_logs = []

    for img in rows:
        current = dict(img)
        start = float(current.get("timestamp_start", 0))
        end = float(current.get("timestamp_end", start))
        duration = end - start

        current["timestamp_start"] = round(start, 3)
        current["timestamp_end"] = round(end, 3)

        if duration >= min_duration:
            smoothed.append(current)
            continue

        scene_num = current.get("scene_number", "?")

        if smoothed:
            # Merge this short visual into the previous image interval.
            prev = smoothed[-1]
            old_end = float(prev["timestamp_end"])
            prev["timestamp_end"] = round(max(old_end, end), 3)

            merged_logs.append(
                f"Scene {int(scene_num):02d} was {duration:.2f}s, merged into previous image"
            )
        else:
            # First image is short. Keep it for now; we may merge it with the next one below.
            current["_too_short_first"] = True
            smoothed.append(current)

    # If the first scene was too short and there is another image, merge first into second.
    if len(smoothed) >= 2 and smoothed[0].get("_too_short_first"):
        first = smoothed.pop(0)
        smoothed[0]["timestamp_start"] = round(float(first["timestamp_start"]), 3)
        merged_logs.append(
            f"Scene {int(first.get('scene_number', 0)):02d} was merged into next image"
        )

    # Clean helper flag
    for img in smoothed:
        img.pop("_too_short_first", None)

    # Make sure intervals stay continuous.
    for i in range(len(smoothed) - 1):
        smoothed[i]["timestamp_end"] = round(float(smoothed[i + 1]["timestamp_start"]), 3)

    if rows:
        smoothed[-1]["timestamp_end"] = round(float(rows[-1]["timestamp_end"]), 3)

    if merged_logs:
        print("   🧩 Image timing smoothing:")
        for log in merged_logs:
            print(f"      {log}")
    else:
        print(f"   ✅ No image intervals under {min_duration:.1f}s")

    print("   🕒 Smoothed image timing report:")
    for img in smoothed:
        start = float(img["timestamp_start"])
        end = float(img["timestamp_end"])
        print(
            f"      Image scene {int(img['scene_number']):02d}: "
            f"{start:>6.2f}s → {end:>6.2f}s "
            f"({end - start:.2f}s)"
        )

    return smoothed

def _is_assembly_only(
    *,
    rebuild_assembly: bool,
    rebuild_all: bool,
    rebuild_script: bool,
    rebuild_series_scripts: bool,
    rebuild_audio: bool,
    rebuild_captions: bool,
    rebuild_images: bool,
    rebuild_thumbnail: bool,
    rebuild_research: bool,
) -> bool:
    """True when only re-rendering assembly from existing cached assets."""
    if rebuild_all or not rebuild_assembly:
        return False
    return not any(
        [
            rebuild_script,
            rebuild_series_scripts,
            rebuild_audio,
            rebuild_captions,
            rebuild_images,
            rebuild_thumbnail,
            rebuild_research,
        ]
    )


def _validate_assembly_assets(
    script_path: str,
    audio_path: str,
    captions_path: str,
    script: dict,
    prefix: str,
) -> None:
    """Fail fast with exact missing paths for assembly-only runs."""
    missing: list[str] = []

    if not os.path.isfile(script_path):
        missing.append(script_path)
    if not os.path.isfile(audio_path):
        missing.append(audio_path)
    if not os.path.isfile(captions_path):
        missing.append(captions_path)

    for scene in script.get("scenes", []):
        scene_num = int(scene.get("scene_number", 0))
        scene_image_path = os.path.join(TEMP_DIR, f"{prefix}scene_{scene_num:02d}.jpg")
        if not os.path.isfile(scene_image_path):
            missing.append(scene_image_path)

    if missing:
        lines = "\n  - ".join(missing)
        raise FileNotFoundError(
            f"Assembly-only mode cannot continue. Missing required file(s):\n  - {lines}"
        )


def _find_latest_video_for_topic(topic_id: str) -> str | None:
    """Find the newest locally rendered video for --post."""
    output_dir = Path(OUTPUT_DIR)
    if not output_dir.exists():
        return None

    matches = list(output_dir.glob(f"{prefix}*.mp4"))
    if not matches:
        return None

    latest = max(matches, key=lambda p: p.stat().st_mtime)
    return str(latest)


# -----------------------------
# Main pipeline
# -----------------------------

def run_pipeline(
    topic: dict,
    preview_only: bool = False,
    yes: bool = False,
    rebuild_script: bool = False,
    rebuild_series_scripts: bool = False,
    rebuild_audio: bool = False,
    rebuild_captions: bool = False,
    rebuild_images: bool = False,
    rebuild_thumbnail: bool = False,
    rebuild_assembly: bool = False,
    rebuild_all: bool = False,
    rebuild_research: bool = False,
    post_only: bool = False,
    build_series: bool = False,
    episode_num: int | None = None,
):
    """Run the video generation pipeline for a single topic."""

    print(f"\n{'=' * 60}")
    print(f"🎬 TOPIC: {topic['title']}")
    if _is_series_topic(topic):
        print(f"   Series mode enabled")
        if episode_num:
            print(f"   Processing Episode {episode_num}")
    print(f"{'=' * 60}\n")

    start_time = datetime.now()
    topic_id = str(topic["id"])

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    # Series Validation
    if _is_series_topic(topic):
        if rebuild_script:
            print("⚠️  --rebuild-script is blocked for series. Use --build-series to ensure continuity.")
            return

        if build_series:
            print("\n✍️  Building series script package...")
            from modules.script import generate_series_script_package
            # Need to determine overwrite_all behavior. Using rebuild_series_scripts if set, else False
            generate_series_script_package(topic, overwrite_all=rebuild_series_scripts, rebuild_research=rebuild_research)
            print("\n✅ Series build complete.")
            return

        if not episode_num:
            print("⚠️  For series, you must specify either --build-series (to write scripts) or --episode X (to render video).")
            return

        # NEW: Validate episode_num against resolved_episode_count
        package_path = get_series_package_path(topic)
        if os.path.isfile(package_path):
            try:
                with open(package_path, "r", encoding="utf-8") as f:
                    package_data = json.load(f)
                    resolved = package_data.get("resolved_episode_count")
                    if resolved and episode_num > resolved:
                        print(f"❌ Episode {episode_num} does not exist. This series currently has {resolved} episodes.")
                        print("   Rebuild the series or choose an episode between 1 and {resolved}.")
                        return
            except Exception as e:
                print(f"⚠️  Warning reading series package: {e}")

    script_path = get_script_path(topic, episode_num)
    prefix = get_asset_prefix(topic, episode_num)
    
    audio_path = os.path.join(TEMP_DIR, f"{prefix}voiceover.mp3")
    captions_path = os.path.join(TEMP_DIR, f"{prefix}captions.json")
    thumbnail_path = os.path.join(TEMP_DIR, f"{prefix}thumbnail.jpg")
    meta_path = os.path.join(TEMP_DIR, f"{prefix}cache_meta.json")

    try:
        # ----------------------------------------
        # Optional: publish previously rendered file only
        # ----------------------------------------
        if post_only:
            script = _load_json(script_path)
            if not script:
                raise FileNotFoundError(f"Script cache not found: {script_path}")

            video_path = _find_latest_video_for_topic(topic_id)
            if not video_path:
                raise FileNotFoundError(f"No rendered video found for topic #{topic_id}")

            print(f"📦 Posting latest local render: {video_path}")
            publish_results = publish_all(video_path, script, thumbnail_path if os.path.isfile(thumbnail_path) else None)
            init_logs()
            log_video(topic, script, publish_results, video_path)
            mark_topic_done(topic_id)
            print("\n🎉 COMPLETE! Existing video posted.")
            return

        # Normalize rebuild flags
        if rebuild_all:
            rebuild_script = True
            rebuild_audio = True
            rebuild_captions = True
            rebuild_images = True
            rebuild_thumbnail = True
            rebuild_assembly = True

        if rebuild_series_scripts:
            _delete_file(script_path)
            # A connected series rewrite changes this topic script,
            # so this topic's generated assets are no longer trusted.
            rebuild_audio = True
            rebuild_captions = True
            rebuild_images = True
            rebuild_thumbnail = True
            rebuild_assembly = True

        elif rebuild_script:
            _delete_file(script_path)
            # A new script means dependent generated assets should not be trusted.
            rebuild_audio = True
            rebuild_captions = True
            rebuild_images = True
            rebuild_thumbnail = True
            rebuild_assembly = True

        if rebuild_audio:
            _delete_file(audio_path)
            # Captions are generated from audio, so they must be refreshed too.
            rebuild_captions = True
            rebuild_assembly = True

        if rebuild_captions:
            _delete_file(captions_path)
            rebuild_assembly = True

        if rebuild_images:
            _delete_glob(f"{TEMP_DIR}/topic_{topic_id}_scene_*.jpg")
            _delete_file(os.path.join(TEMP_DIR, f"topic_{topic_id}_enhanced_prompts.json"))
            rebuild_assembly = True

        if rebuild_thumbnail:
            _delete_file(thumbnail_path)

        assembly_only = _is_assembly_only(
            rebuild_assembly=rebuild_assembly,
            rebuild_all=rebuild_all,
            rebuild_script=rebuild_script,
            rebuild_series_scripts=rebuild_series_scripts,
            rebuild_audio=rebuild_audio,
            rebuild_captions=rebuild_captions,
            rebuild_images=rebuild_images,
            rebuild_thumbnail=rebuild_thumbnail,
            rebuild_research=rebuild_research,
        )

        if assembly_only:
            print("♻️  Assembly-only mode: reusing existing assets")
            print("   (no script / voiceover / captions / image regeneration)")

        # ----------------------------------------
        # STEP 1: Generate/load script
        # ----------------------------------------
        if _is_series_topic(topic):
            if os.path.isfile(script_path):
                print("⚡ Script loaded from cache")
                script = _load_json(script_path)
                if not script:
                    raise Exception(f"Failed to load existing script from {script_path}")
            else:
                print(f"\n❌ Episode script not found. Run python main.py --topic-id {topic_id} --build-series first.")
                return
        else:
            if os.path.isfile(script_path) and not rebuild_script:
                print("⚡ Script loaded from cache")
                script = _load_json(script_path)
                if not script:
                    raise Exception(f"Failed to load existing script from {script_path}")
            else:
                print("\n✍️  STEP 1/6: Generating script...")
                script = generate_script(topic)
                _save_json(script_path, script)
                print(f"   Script saved: {script_path}")

        if assembly_only:
            _validate_assembly_assets(script_path, audio_path, captions_path, script, prefix)

        narration = script.get("full_narration", "")
        narration_hash = _hash_text(narration)
        cache_meta = _load_json(meta_path, default={}) or {}
        cached_hash = cache_meta.get("narration_hash")

        # 1. Automatic cache safety by hash: script changed -> old audio/captions are invalid.
        if not assembly_only and cached_hash and cached_hash != narration_hash:
            print("⚠️  Script narration changed since the last run (hash mismatch).")
            print("   Invalidating cached voiceover, captions and cache_meta.")
            _delete_file(audio_path)
            _delete_file(captions_path)
            _delete_file(meta_path)
            cache_meta = {}
            cached_hash = None
            rebuild_audio = True
            rebuild_captions = True
            rebuild_assembly = True

        # 2. mtime cascade: do not trust cached files that are older than what
        # produced them, even if the narration hash happens to match.
        script_mtime = _file_mtime(script_path)
        audio_mtime = _file_mtime(audio_path)
        captions_mtime = _file_mtime(captions_path)

        if not assembly_only and audio_mtime and script_mtime > audio_mtime + 1:
            print("⚠️  Script is newer than the cached voiceover.")
            print("   Forcing voiceover + captions regeneration.")
            _delete_file(audio_path)
            _delete_file(captions_path)
            _delete_file(meta_path)
            cache_meta = {}
            rebuild_audio = True
            rebuild_captions = True
            rebuild_assembly = True
        elif (
            not assembly_only
            and captions_mtime
            and audio_mtime > captions_mtime + 1
            and not rebuild_audio
        ):
            print("⚠️  Voiceover is newer than the cached captions.")
            print("   Forcing captions regeneration.")
            _delete_file(captions_path)
            rebuild_captions = True
            rebuild_assembly = True

        # 3. Strong content check: even when hashes/mtimes look fine, verify the
        # cached captions actually start with the current narration. Stale captions
        # from an earlier script of the same topic are caught here.
        if (
            not assembly_only
            and os.path.isfile(captions_path)
            and not rebuild_captions
        ):
            cached_words_for_check = _load_json(captions_path, default=[])
            if not _captions_match_narration_ordered(cached_words_for_check, narration):
                print("⚠️  Cached captions do not match the current script narration "
                      "(ordered content check failed).")
                print("   Invalidating cached captions and cache_meta.")
                _delete_file(captions_path)
                _delete_file(meta_path)
                cache_meta = {}
                rebuild_captions = True
                rebuild_assembly = True

        # ----------------------------------------
        # STEP 2: Generate/load voiceover
        # ----------------------------------------
        if assembly_only or (os.path.isfile(audio_path) and not rebuild_audio):
            if not os.path.isfile(audio_path):
                raise FileNotFoundError(f"Assembly-only mode: voiceover missing: {audio_path}")
            print("⚡ Voiceover loaded from cache "
                  "(script hash matches and audio is at least as new as script)")
        else:
            print("\n🎙️  STEP 2/6: Generating voiceover...")
            audio_path = generate_voiceover(
                narration=narration,
                output_filename=f"{prefix}voiceover.mp3",
            )
            # Audio just changed -> any cached captions belong to the previous audio.
            if os.path.isfile(captions_path):
                print("   New voiceover detected — invalidating cached captions.")
                _delete_file(captions_path)
            rebuild_captions = True
            rebuild_assembly = True

        # NOTE: narration_hash is intentionally NOT saved here. It is saved only
        # after captions have been generated/validated to belong to this script
        # (see end of STEP 4). Saving early was the bug that let stale captions
        # silently survive across runs.

        # ----------------------------------------
        # ----------------------------------------
        # STEP 3: Generate/load images
        # ----------------------------------------
        print("\n🎨 STEP 3/6: Preparing images...")
        image_paths = []

        scenes_for_images = script.get("scenes", [])
        should_prepare_prompts = rebuild_images and not assembly_only
        if not should_prepare_prompts and not assembly_only:
            for _scene in scenes_for_images:
                _scene_num = int(_scene["scene_number"])
                _scene_image_path = os.path.join(
                    TEMP_DIR,
                    f"{prefix}scene_{_scene_num:02d}.jpg"
                )
                if not os.path.isfile(_scene_image_path):
                    should_prepare_prompts = True
                    break

        if should_prepare_prompts:
            prepare_scene_image_prompts(script, prefix, force=rebuild_images)

        for scene in scenes_for_images:
            scene_num = int(scene["scene_number"])
            scene_image_path = os.path.join(
                TEMP_DIR,
                f"{prefix}scene_{scene_num:02d}.jpg"
            )

            if assembly_only or (os.path.isfile(scene_image_path) and not rebuild_images):
                if not os.path.isfile(scene_image_path):
                    raise FileNotFoundError(
                        f"Assembly-only mode: scene image missing: {scene_image_path}"
                    )
                print(f"⚡ Scene {scene_num} image loaded from cache")
                path = scene_image_path
            else:
                try:
                    path = generate_image(scene, scene_num, prefix, script, force=rebuild_images)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  ❌ Scene {scene_num} failed after all retry attempts: {e}")
                    raise

            image_paths.append(
                {
                    "scene_number": scene_num,
                    "path": path,
                    "timestamp_start": float(scene.get("timestamp_start", 0)),
                    "timestamp_end": float(scene.get("timestamp_end", 0)),
                }
            )

        print(f"   ✅ Images ready: {len(image_paths)} scenes")

        # ----------------------------------------
        # STEP 4: Generate/load captions
        # ----------------------------------------
        if assembly_only or (os.path.isfile(captions_path) and not rebuild_captions):
            if not os.path.isfile(captions_path):
                raise FileNotFoundError(f"Assembly-only mode: captions missing: {captions_path}")
            print("⚡ Captions loaded from cache (passed ordered content check)")
            words = _load_json(captions_path, default=[])
        else:
            print("\n📄 STEP 4/6: Generating captions...")
            words = generate_captions(audio_path, prefix)

        # Final defensive check: even captions we just generated must align with
        # the current narration. If they do not, the audio itself is wrong and we
        # surface the problem instead of saving a misleading cache_meta.
        if not _captions_match_narration_ordered(words, narration):
            print("⚠️  Newly generated captions still do not align with the script "
                  "narration. Forcing voiceover + captions regeneration on the "
                  "next run.")
            _delete_file(meta_path)
        else:
            cache_meta["narration_hash"] = narration_hash
            cache_meta["last_audio_path"] = audio_path
            cache_meta["captions_validated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_json(meta_path, cache_meta)
            print(f"   ✅ cache_meta updated (narration_hash={narration_hash})")

        captions = format_captions_for_creatomate(words)
        video_duration = _duration_from_words(words, fallback_seconds=90.0)
        image_paths = _align_image_paths_to_words(words, script, image_paths, video_duration)
        image_paths = _merge_short_image_intervals(image_paths, min_duration=3.0)

        print(f"   Caption chunks: {len(captions)}")
        print(f"   🎯 Final render duration from captions/audio: {video_duration}s")

        # ----------------------------------------
        # STEP 5: Generate/load thumbnail
        # ----------------------------------------
        skip_thumbnail = assembly_only and not GENERATE_THUMBNAIL
        if skip_thumbnail:
            print("⚡ Thumbnail skipped (assembly-only, GENERATE_THUMBNAIL=False)")
        elif os.path.isfile(thumbnail_path) and not rebuild_thumbnail:
            print("⚡ Thumbnail loaded from cache")
        else:
            print("\n🖼️  STEP 5/6: Generating thumbnail...")
            thumbnail_prompt = generate_thumbnail_prompt(script)
            thumbnail_path = generate_thumbnail_image(thumbnail_prompt, prefix)

        # ----------------------------------------
        # STEP 6: Assemble video
        # ----------------------------------------
        print("\n🎬 STEP 6/6: Assembling video...")
        if rebuild_assembly:
            print("   Re-rendering assembly with existing usable assets.")

        video_path = assemble_video(
            script=script,
            image_paths=image_paths,
            audio_path=audio_path,
            captions=captions,
            prefix=prefix,
            thumbnail_path=thumbnail_path,
            video_duration=video_duration,
        )

        # ----------------------------------------
        # DONE: Show preview or publish
        # ----------------------------------------
        elapsed = (datetime.now() - start_time).seconds
        print(f"\n✨ Video generated in {elapsed}s")
        print(f"   File: {video_path}")
        print(f"   Size: {os.path.getsize(video_path) / 1024 / 1024:.1f} MB")

        if preview_only:
            print("\n🔍 PREVIEW MODE — video not posted")
            print(f"   Review your video at: {video_path}")
            print(f"   To post latest render: python main.py --topic-id {topic_id} --post")
            return

        print("\n📱 Ready to post to YouTube, TikTok, and Instagram")
        if yes:
            confirm = "y"
        else:
            confirm = input("   Post now? (y/n): ").strip().lower()

        if confirm != "y":
            print("   Skipped. Video saved locally.")
            return

        publish_results = publish_all(video_path, script, thumbnail_path)
        init_logs()
        log_video(topic, script, publish_results, video_path)
        mark_topic_done(topic_id)
        cleanup_temp()

        print("\n🎉 COMPLETE! Video posted to all platforms.")
        print("   Run 'python main.py --analytics' in 48 hours to track performance.")

    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(description="Ash & Empires Video Generator")
    parser.add_argument("--build-series", action="store_true", help="Generate all episode scripts for a series")
    parser.add_argument("--episode", type=int, help="Episode number to process for a series")
    parser.add_argument("--topic-id", type=str, help="Generate specific topic by ID")
    parser.add_argument("--preview", action="store_true", help="Generate but don't post")
    parser.add_argument("--post", action="store_true", help="Post previously generated local video")
    parser.add_argument("--yes", action="store_true", help="Auto-confirm posting prompt")
    parser.add_argument("--list", action="store_true", help="List all topics")
    parser.add_argument("--analytics", action="store_true", help="Update performance analytics")
    parser.add_argument("--voices", action="store_true", help="List ElevenLabs voices")
    parser.add_argument("--summary", action="store_true", help="Show performance summary")

    # Cache/testing controls
    parser.add_argument("--rebuild-script", action="store_true", help="Regenerate script and all dependent assets")
    parser.add_argument(
        "--rebuild-series-scripts",
        action="store_true",
        help="Regenerate connected scripts for the current topic's series",
    )
    parser.add_argument("--rebuild-audio", action="store_true", help="Regenerate voiceover and captions")
    parser.add_argument("--rebuild-captions", action="store_true", help="Regenerate captions from existing/new audio")
    parser.add_argument("--rebuild-images", action="store_true", help="Regenerate scene images")
    parser.add_argument("--rebuild-thumbnail", action="store_true", help="Regenerate thumbnail")
    parser.add_argument("--rebuild-assembly", action="store_true", help="Re-render the video using cached assets")
    parser.add_argument("--rebuild-all", action="store_true", help="Regenerate everything from scratch")
    parser.add_argument(
        "--rebuild-research",
        action="store_true",
        help="Regenerate the series research story bible (does not automatically rebuild video assets)",
    )
    parser.add_argument(
        "--research-only",
        action="store_true",
        help="Generate or load series research for the topic then exit without producing scripts, audio, or video",
    )

    args = parser.parse_args()

    if args.list:
        list_topics()
        return

    if args.analytics:
        print("📊 Updating performance analytics...")
        update_performance()
        print_performance_summary()
        return

    if args.summary:
        print_performance_summary()
        return

    if args.voices:
        get_available_voices()
        return

    topics = load_topics()

    if args.topic_id:
        topic = get_topic_by_id(topics, args.topic_id)
        if not topic:
            print(f"❌ Topic #{args.topic_id} not found")
            return
    else:
        topic = get_next_topic(topics)
        if not topic:
            print("✅ All topics completed! Add more to topics.csv")
            return

    # --research-only: generate/load research then exit.
    if args.research_only:
        series_name = str(topic.get("series", "") or "").strip()
        if not series_name or topic.get("part", "0") == "0":
            print("⚠️  --research-only requires a series topic (series column must be set and part != 0).")
            return
        from modules.script import _load_series_topics
        series_topics_list = _load_series_topics(series_name)
        research = ensure_series_research(
            series_name,
            series_topics_list,
            rebuild=args.rebuild_research,
        )
        path = research_path_for_series(series_name)
        print(f"\n📚 Research ready: {path}")
        if research:
            print(f"   Series:  {research.get('series', series_name)}")
            print(f"   Updated: {research.get('research_last_updated', 'unknown')}")
            print(f"   Timeline events: {len(research.get('historical_timeline', []))}")
            print(f"   Must-not-imply rules: {len(research.get('must_not_imply_facts', []))}")
        return

    run_pipeline(
        topic,
        preview_only=args.preview,
        yes=args.yes,
        rebuild_script=args.rebuild_script,
        rebuild_series_scripts=args.rebuild_series_scripts,
        rebuild_audio=args.rebuild_audio,
        rebuild_captions=args.rebuild_captions,
        rebuild_images=args.rebuild_images,
        rebuild_thumbnail=args.rebuild_thumbnail,
        rebuild_assembly=args.rebuild_assembly,
        rebuild_all=args.rebuild_all,
        rebuild_research=args.rebuild_research,
        post_only=args.post,
        build_series=args.build_series,
        episode_num=args.episode,
    )


if __name__ == "__main__":
    main()
