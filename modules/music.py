import os
import json
import random
import re
import time
from pathlib import Path

try:
    from config import MUSIC_SELECTION_MODE
except ImportError:
    MUSIC_SELECTION_MODE = "tagged_random"

try:
    from config import MUSIC_LIBRARY_FILE
except ImportError:
    MUSIC_LIBRARY_FILE = "music/music_library.json"

try:
    from config import MUSIC_LIBRARY_DIR
except ImportError:
    MUSIC_LIBRARY_DIR = "music/library"

try:
    from config import DEFAULT_MUSIC_TAGS
except ImportError:
    DEFAULT_MUSIC_TAGS = ["dark", "cinematic", "history"]

try:
    from config import BACKGROUND_MUSIC_VOLUME_MULTIPLIER
except ImportError:
    BACKGROUND_MUSIC_VOLUME_MULTIPLIER = 0.18

try:
    from config import ENABLE_SOUND_EFFECTS
except ImportError:
    ENABLE_SOUND_EFFECTS = False

try:
    from config import SFX_LIBRARY_FILE
except ImportError:
    SFX_LIBRARY_FILE = "music/sfx_library.json"

try:
    from config import SFX_LIBRARY_DIR
except ImportError:
    SFX_LIBRARY_DIR = "music/sfx"

try:
    from config import MAX_SFX_PER_VIDEO
except ImportError:
    MAX_SFX_PER_VIDEO = 3

try:
    from config import SFX_VOLUME_MULTIPLIER
except ImportError:
    SFX_VOLUME_MULTIPLIER = 0.08

try:
    from config import TEMP_DIR
except ImportError:
    TEMP_DIR = "temp"


def load_music_library() -> list:
    """Load the manual music library from JSON file."""
    path = Path(MUSIC_LIBRARY_FILE)
    if not path.is_file():
        print(f"   ⚠️ Music library file not found: {MUSIC_LIBRARY_FILE}")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"   ⚠️ Error reading music library JSON: {e}")
        return []


def load_sfx_library() -> list:
    """Load the manual sound effects library from JSON file."""
    path = Path(SFX_LIBRARY_FILE)
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"   ⚠️ Error reading SFX library JSON: {e}")
        return []


_SFX_FILENAME_RULES = [
    ("low_boom", {"sfx_type": "low_boom", "best_for": ["hook", "twist", "ending"]}),
    ("deep_hit", {"sfx_type": "deep_hit", "best_for": ["hook"]}),
    ("subtle_whoosh", {"sfx_type": "whoosh", "best_for": ["twist"]}),
    ("whoosh", {"sfx_type": "whoosh", "best_for": ["twist"]}),
    ("dark_riser", {"sfx_type": "riser", "best_for": ["ending"]}),
    ("distant_rumble", {"sfx_type": "rumble", "best_for": ["ending"]}),
    ("distant_drum", {"sfx_type": "drum", "best_for": ["twist"]}),
    ("wind_ambience", {"sfx_type": "ambience", "best_for": ["hook"]}),
    ("metal_hit", {"sfx_type": "metal", "best_for": ["twist"]}),
]

_MOMENT_SFX_PRIORITY = {
    "hook": ["low_boom", "deep_hit", "wind_ambience"],
    "twist": ["subtle_whoosh", "whoosh", "distant_drum", "metal_hit"],
    "ending": ["dark_riser", "distant_rumble", "low_boom"],
}

# Normalized filename hints (matches "low boom.mp3", "low_boom_1.wav", etc.)
_MOMENT_PREFERRED_KEYS = {
    "hook": ["lowboom", "deephit"],
    "twist": ["subtlewhoosh", "distantdrum", "distantdrumhit"],
    "ending": ["darkriser", "distantrumble", "cinematicriser"],
}

_SFX_LOG_LABEL = {
    "hook": "hook",
    "twist": "middle",
    "ending": "ending",
}


def _normalize_sfx_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _infer_sfx_meta_from_filename(filename: str) -> dict:
    name = filename.lower()
    norm = _normalize_sfx_key(Path(name).stem)
    meta = {
        "filename": filename,
        "sfx_type": "generic",
        "best_for": ["hook", "twist", "ending"],
        "volume_multiplier": SFX_VOLUME_MULTIPLIER,
    }
    for needle, rule in _SFX_FILENAME_RULES:
        if _normalize_sfx_key(needle) in norm:
            meta.update(rule)
            break
    return meta


def _discover_sfx_on_disk() -> list[dict]:
    sfx_dir = Path(SFX_LIBRARY_DIR)
    if not sfx_dir.is_dir():
        return []

    discovered = []
    for path in sorted(sfx_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".wav", ".mp3", ".m4a", ".ogg"):
            continue
        if path.name.lower().startswith("example_"):
            continue
        entry = _infer_sfx_meta_from_filename(path.name)
        entry["filename"] = path.name
        discovered.append(entry)
    return discovered


def _merge_sfx_catalog(json_entries: list, disk_entries: list) -> list[dict]:
    by_name: dict[str, dict] = {}
    for item in json_entries:
        if not isinstance(item, dict):
            continue
        fn = item.get("filename") or item.get("file")
        if not fn:
            continue
        if Path(get_sfx_path(fn)).is_file():
            merged = dict(item)
            merged["filename"] = fn
            by_name[fn] = merged

    for item in disk_entries:
        fn = item.get("filename")
        if fn and fn not in by_name and Path(get_sfx_path(fn)).is_file():
            by_name[fn] = item

    return list(by_name.values())


def log_sfx_status() -> None:
    if ENABLE_SOUND_EFFECTS:
        print("   Sound effects enabled")
        sfx_dir = Path(SFX_LIBRARY_DIR)
        if sfx_dir.is_dir():
            count = sum(
                1
                for p in sfx_dir.iterdir()
                if p.is_file()
                and p.suffix.lower() in (".wav", ".mp3", ".m4a", ".ogg")
                and not p.name.lower().startswith("example_")
            )
            print(f"   Local SFX library: {SFX_LIBRARY_DIR} ({count} file(s))")
    else:
        print("   Sound effects disabled")


def _script_mentions_metal(script: dict) -> bool:
    text = " ".join(
        [
            str(script.get("title", "")),
            str(script.get("full_narration", "")),
            str(script.get("hook", "")),
        ]
    ).lower()
    needles = ("metal", "armor", "armour", "sword", "shield", "clash", "blade")
    return any(n in text for n in needles)


def _pick_sfx_for_moment(moment_type: str, valid_sfx: list[dict], script: dict) -> dict | None:
    for pref_key in _MOMENT_PREFERRED_KEYS.get(moment_type, []):
        if "metal" in pref_key and not _script_mentions_metal(script):
            continue
        if "wind" in pref_key and moment_type != "hook":
            continue
        for sfx in valid_sfx:
            fn = str(sfx.get("filename", ""))
            norm = _normalize_sfx_key(fn)
            if pref_key not in norm:
                continue
            chosen = dict(sfx)
            if "wind" in pref_key or "ambience" in pref_key:
                try:
                    base_vol = float(chosen.get("volume_multiplier", SFX_VOLUME_MULTIPLIER))
                except (TypeError, ValueError):
                    base_vol = SFX_VOLUME_MULTIPLIER
                chosen["volume_multiplier"] = min(base_vol, SFX_VOLUME_MULTIPLIER * 0.6)
            return chosen

    for pref in _MOMENT_SFX_PRIORITY.get(moment_type, []):
        if pref == "metal_hit" and not _script_mentions_metal(script):
            continue
        if pref == "wind_ambience" and moment_type != "hook":
            continue
        pref_key = _normalize_sfx_key(pref)
        for sfx in valid_sfx:
            fn = str(sfx.get("filename", ""))
            norm = _normalize_sfx_key(fn)
            st = _normalize_sfx_key(str(sfx.get("sfx_type", "")))
            if pref_key in norm or pref_key == st:
                return sfx

    for sfx in valid_sfx:
        best = [str(b).lower() for b in sfx.get("best_for", [])]
        if moment_type in best:
            return sfx

    return None


def _sfx_file_exists(sfx: dict) -> bool:
    filename = sfx.get("filename") or sfx.get("file")
    if not filename:
        return False
    return Path(get_sfx_path(filename)).is_file()


def get_music_path(filename: str) -> str:
    """Get the full path to a local music track file."""
    return str(Path(MUSIC_LIBRARY_DIR) / filename)


def get_sfx_path(filename: str) -> str:
    """Get the full path to a local sound effect file."""
    return str(Path(SFX_LIBRARY_DIR) / filename)


def choose_music_track(script: dict, topic: dict | None = None, preferred_tags: list | None = None) -> tuple[str | None, float]:
    """
    Selects a music track intelligently based on mood/context matching, or fallbacks.
    Returns (music_file_path, volume_multiplier).
    """
    library = load_music_library()
    if not library:
        return _fallback_fixed_first(library)

    mode = MUSIC_SELECTION_MODE.lower().strip()

    # Mode 1: Fixed behavior (old behavior)
    if mode == "fixed":
        return _fallback_fixed_first(library)

    # Mode 2: Pure Random
    if mode == "random":
        valid_tracks = [t for t in library if _track_exists(t)]
        if not valid_tracks:
            return _fallback_fixed_first(library)
        track = random.choice(valid_tracks)
        volume = track.get("volume_multiplier", BACKGROUND_MUSIC_VOLUME_MULTIPLIER)
        path = get_music_path(track["filename"])
        print(f"🎵 Random music selected: {track['filename']} at volume {volume}")
        return path, volume

    # Mode 3: Tagged Random (Intelligent selection)
    # 1. Build a text block from the script and topic for tag matching
    topic_dict = topic or {}
    text_parts = [
        str(topic_dict.get("title", "")),
        str(topic_dict.get("description", "")),
        str(script.get("title", "")),
        " ".join(script.get("hashtags", [])),
        str(script.get("full_narration", "")),
    ]
    # Add any extra manual mood tags from script/topic if present
    if "mood_tags" in script:
        text_parts.extend(script["mood_tags"])
    if "mood_tags" in topic_dict:
        text_parts.extend(topic_dict["mood_tags"])

    search_text = " ".join(text_parts).lower()
    words = set(re.findall(r"[a-z0-9]+", search_text))

    # 2. Score each track in the library
    scored_tracks = []
    for track in library:
        if not _track_exists(track):
            continue

        filename = track.get("filename")
        if not filename:
            continue

        # Extract track tags
        mood_tags = [str(t).lower() for t in track.get("mood_tags", [])]
        best_for = [str(t).lower() for t in track.get("best_for", [])]
        avoid_for = [str(t).lower() for t in track.get("avoid_for", [])]

        # Check negative matching (avoid tags)
        avoid_matches = [tag for tag in avoid_for if tag in words]
        if avoid_matches:
            # Explicitly avoid this track
            continue

        # Check positive matching (mood + best_for)
        mood_matches = [tag for tag in mood_tags if tag in words]
        best_matches = [tag for tag in best_for if tag in words]

        # Also score preferred tags if supplied
        pref_matches = []
        if preferred_tags:
            pref_matches = [str(t).lower() for t in preferred_tags if str(t).lower() in mood_tags or str(t).lower() in best_for]

        score = len(mood_matches) * 1.5 + len(best_matches) * 2.0 + len(pref_matches) * 1.0
        matched_tags = list(set(mood_matches + best_matches))

        scored_tracks.append({
            "track": track,
            "score": score,
            "matched_tags": matched_tags
        })

    # Filter out empty scores or sort
    if scored_tracks:
        # Sort by score descending
        scored_tracks.sort(key=lambda x: x["score"], reverse=True)
        max_score = scored_tracks[0]["score"]

        # If we have some matching tracks with score > 0
        if max_score > 0:
            best_candidates = [item for item in scored_tracks if item["score"] == max_score]
            chosen_item = random.choice(best_candidates)
            track = chosen_item["track"]
            volume = track.get("volume_multiplier", BACKGROUND_MUSIC_VOLUME_MULTIPLIER)
            path = get_music_path(track["filename"])
            print(f"🎵 Selected music: {track['filename']} — matched tags: {chosen_item['matched_tags']} (score: {chosen_item['score']})")
            return path, volume

    # If no tags matched or no tracks scored > 0, choose randomly from all valid tracks
    valid_tracks = [t for t in library if _track_exists(t)]
    if valid_tracks:
        track = random.choice(valid_tracks)
        volume = track.get("volume_multiplier", BACKGROUND_MUSIC_VOLUME_MULTIPLIER)
        path = get_music_path(track["filename"])
        print(f"🎵 No tag matches. Selected random fallback music: {track['filename']}")
        return path, volume

    # Absolute fallback to first config example / old behavior
    return _fallback_fixed_first(library)


def _track_exists(track: dict) -> bool:
    """Check if the physical music track file exists locally."""
    filename = track.get("filename")
    if not filename:
        return False
    return Path(get_music_path(filename)).is_file()


def _fallback_fixed_first(library: list) -> tuple[str | None, float]:
    """Old fixed music selection: uses the first valid file from JSON or None."""
    for item in library:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename") or item.get("file")
        if not filename:
            continue
        music_path = Path(get_music_path(filename))
        if music_path.is_file():
            # Support both old volume name "volume" and new "volume_multiplier"
            vol_val = item.get("volume_multiplier") or item.get("volume", BACKGROUND_MUSIC_VOLUME_MULTIPLIER)
            # Parse if string like "10%"
            if isinstance(vol_val, str) and vol_val.endswith("%"):
                try:
                    vol = float(vol_val[:-1]) / 100.0
                except ValueError:
                    vol = BACKGROUND_MUSIC_VOLUME_MULTIPLIER
            else:
                try:
                    vol = float(vol_val)
                except (ValueError, TypeError):
                    vol = BACKGROUND_MUSIC_VOLUME_MULTIPLIER
            print(f"🎵 Old fixed music selected: {music_path} at volume {vol:.2f}")
            return str(music_path), vol

    print("🎵 No valid music file found. Rendering voiceover only.")
    return None, 0.0


def choose_sfx_for_video(script: dict, topic: dict | None = None, image_paths: list | None = None) -> list[dict]:
    """
    Select up to MAX_SFX_PER_VIDEO local SFX at hook / twist / ending timestamps.
    Returns [{'path', 'timestamp', 'volume', 'filename', 'type'}, ...]
    """
    if not ENABLE_SOUND_EFFECTS:
        return []

    json_lib = load_sfx_library()
    disk_lib = _discover_sfx_on_disk()
    catalog = _merge_sfx_catalog(json_lib, disk_lib)

    valid_sfx = [s for s in catalog if _sfx_file_exists(s)]
    if not valid_sfx:
        print("   No valid SFX found; skipping SFX")
        return []

    scenes = sorted(image_paths or [], key=lambda x: int(x.get("scene_number", 0)))
    if not scenes:
        print("   No scene timings for SFX, skipping SFX")
        return []

    moments: list[dict] = [
        {"type": "hook", "timestamp": float(scenes[0].get("timestamp_start", 0.0))},
    ]
    if len(scenes) >= 3:
        mid_idx = len(scenes) // 2
        moments.append({
            "type": "twist",
            "timestamp": float(scenes[mid_idx].get("timestamp_start", 0.0)),
        })
    if len(scenes) >= 2:
        moments.append({
            "type": "ending",
            "timestamp": float(scenes[-1].get("timestamp_start", 0.0)),
        })

    cap = min(int(MAX_SFX_PER_VIDEO or 3), 3)
    moments = moments[:cap]

    selected: list[dict] = []
    used_files: set[str] = set()

    for moment in moments:
        m_type = moment["type"]
        log_label = _SFX_LOG_LABEL.get(m_type, m_type)
        try:
            chosen = _pick_sfx_for_moment(m_type, valid_sfx, script)
            if not chosen:
                continue
            fn = chosen["filename"]
            if fn in used_files:
                continue

            vol = chosen.get("volume_multiplier", SFX_VOLUME_MULTIPLIER)
            try:
                vol = float(vol)
            except (TypeError, ValueError):
                vol = SFX_VOLUME_MULTIPLIER
            vol = min(vol, SFX_VOLUME_MULTIPLIER * 1.5)

            path = get_sfx_path(fn)
            if not Path(path).is_file():
                print(f"   ⚠️  SFX file missing, skipping: {fn}")
                continue

            used_files.add(fn)
            selected.append({
                "path": path,
                "filename": fn,
                "timestamp": float(moment["timestamp"]),
                "volume": vol,
                "type": m_type,
            })
            print(f"   Selected SFX for {log_label}: {fn}")
        except Exception as exc:
            print(f"   ⚠️  SFX selection failed for {log_label}, skipping ({exc})")
            continue

    if not selected:
        print("   No valid SFX found; skipping SFX")

    return selected


def save_music_selection_metadata(prefix: str, music_path: str | None, music_volume: float, sfx_selections: list):
    """Save chosen tracks and sfx metadata to a JSON file in the temp directory."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    meta_path = Path(TEMP_DIR) / f"{prefix}music_selection.json"
    meta = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "music": {
            "path": music_path,
            "filename": Path(music_path).name if music_path else None,
            "volume": music_volume,
        },
        "sfx": [
            {
                "path": s["path"],
                "filename": s["filename"],
                "timestamp": s["timestamp"],
                "volume": s["volume"],
                "type": s["type"],
            }
            for s in sfx_selections
        ],
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"   ⚠️ Could not save music selection metadata: {e}")
