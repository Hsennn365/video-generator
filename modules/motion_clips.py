"""Opt-in local video-clip replacements for static image scenes.

No API calls, no subscriptions. Existing videos must be downloaded/created
by the user and their licences checked before adding to the manifest.
"""
import json
from pathlib import Path

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def load_motion_clips(prefix: str, temp_dir: str) -> dict[int, str]:
    """Read optional <temp_dir>/<prefix>motion_clips.json; missing means no change.

    Format:
        {"scenes":[{"scene_number":1,"path":"temp/scene_1_motion.mp4"}]}
    """
    manifest = Path(temp_dir) / f"{prefix}motion_clips.json"
    if not manifest.exists():
        return {}

    document = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("scenes"), list):
        raise ValueError(f"Motion manifest must contain a scenes array: {manifest}")

    result: dict[int, str] = {}
    for item in document["scenes"]:
        if not isinstance(item, dict):
            raise ValueError("Motion manifest scenes must be objects")
        try:
            number = int(item["scene_number"])
            raw_path = str(item["path"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid motion manifest entry: {item}") from exc
        if number <= 0 or number in result or not raw_path.strip():
            raise ValueError(f"Invalid/duplicate motion scene number: {number}")
        file = Path(raw_path).expanduser()
        if file.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Not a supported video format: {file}")
        if not file.is_file() or file.stat().st_size < 4096:
            raise FileNotFoundError(f"Missing or invalid motion clip for scene {number}: {file}")
        result[number] = str(file.resolve())

    return result


def render_motion_clip(
    ffmpeg: str,
    input_path: str,
    output_path: str,
    duration: float,
    fps: int,
    width: int,
    height: int,
    encoder_args: list[str],
    runner,
) -> None:
    """Normalize a preexisting local motion clip to the still-image clip format.

    For clips shorter than the narration beat, briefly holds the last frame
    instead of looping an action unnaturally. Input audio is intentionally
    discarded: the pipeline's own narration and music remain authoritative.
    """
    seconds = float(duration)
    if not 0.2 <= seconds <= 300:
        raise ValueError(f"Invalid scene duration: {seconds}")
    if width < 180 or height < 180 or fps < 12:
        raise ValueError("Invalid scene resolution or fps")

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,"
        f"fps={fps},"
        f"tpad=stop_mode=clone:stop_duration={seconds:.3f},"
        f"trim=duration={seconds:.3f},setpts=PTS-STARTPTS,"
        f"format=yuv420p"
    )
    cmd = [
        ffmpeg, "-y", "-i", input_path, "-t", str(seconds),
        "-vf", vf, "-an", *encoder_args,
        "-pix_fmt", "yuv420p", output_path,
    ]
    runner(cmd, f"Normalize generated motion clip {Path(input_path).name}")
