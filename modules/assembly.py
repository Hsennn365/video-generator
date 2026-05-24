import json
import os
import random
import re
import subprocess
import time
from pathlib import Path

from config import OUTPUT_DIR, TEMP_DIR, VIDEO_HEIGHT, VIDEO_WIDTH

try:
    from config import ENABLE_IMAGE_MOTION
except ImportError:
    ENABLE_IMAGE_MOTION = False

try:
    from config import ENABLE_KEY_SCENE_MOTION
except ImportError:
    ENABLE_KEY_SCENE_MOTION = True

try:
    from config import KEY_SCENE_MOTION_ONLY
except ImportError:
    KEY_SCENE_MOTION_ONLY = True

try:
    from config import KEY_SCENE_MOTION_MAX_SCENES
except ImportError:
    KEY_SCENE_MOTION_MAX_SCENES = 3

try:
    from config import KEY_SCENE_MOTION_INCLUDE_FIRST
except ImportError:
    KEY_SCENE_MOTION_INCLUDE_FIRST = True

try:
    from config import KEY_SCENE_MOTION_INCLUDE_LAST
except ImportError:
    KEY_SCENE_MOTION_INCLUDE_LAST = True

try:
    from config import KEY_SCENE_MOTION_ZOOM_MAX
except ImportError:
    KEY_SCENE_MOTION_ZOOM_MAX = 1.03

try:
    from config import KEY_SCENE_MOTION_FPS
except ImportError:
    KEY_SCENE_MOTION_FPS = 24

try:
    from config import KEY_SCENE_MOTION_PRESET
except ImportError:
    KEY_SCENE_MOTION_PRESET = "ultrafast"

try:
    from config import KEY_SCENE_MOTION_USE_GPU_ENCODER
except ImportError:
    KEY_SCENE_MOTION_USE_GPU_ENCODER = True

try:
    from config import KEY_SCENE_MOTION_TIMEOUT_SECONDS
except ImportError:
    KEY_SCENE_MOTION_TIMEOUT_SECONDS = 300

try:
    from config import KEY_SCENE_MOTION_TOTAL_TIMEOUT_SECONDS
except ImportError:
    KEY_SCENE_MOTION_TOTAL_TIMEOUT_SECONDS = 900

# Legacy fallback
try:
    from config import IMAGE_MOTION_SCENE_TIMEOUT_SEC
except ImportError:
    IMAGE_MOTION_SCENE_TIMEOUT_SEC = None

try:
    from config import ENABLE_SCENE_TRANSITIONS
except ImportError:
    ENABLE_SCENE_TRANSITIONS = True

try:
    from config import SCENE_TRANSITION_TYPE
except ImportError:
    SCENE_TRANSITION_TYPE = "fade"

try:
    from config import SCENE_TRANSITION_DURATION
except ImportError:
    SCENE_TRANSITION_DURATION = 0.25

try:
    from config import SCENE_TRANSITION_RANDOMIZE
except ImportError:
    SCENE_TRANSITION_RANDOMIZE = False

try:
    from config import SCENE_TRANSITION_OPTIONS
except ImportError:
    SCENE_TRANSITION_OPTIONS = ["fade", "crossfade", "dip_to_black"]

try:
    from config import ENABLE_PROFESSIONAL_MOTION
except ImportError:
    ENABLE_PROFESSIONAL_MOTION = True

try:
    from config import PRO_MOTION_STYLE
except ImportError:
    PRO_MOTION_STYLE = "static_plus_push"

try:
    from config import PRO_MOTION_MAX_SCENES
except ImportError:
    PRO_MOTION_MAX_SCENES = 3

try:
    from config import PRO_MOTION_ZOOM_MAX
except ImportError:
    PRO_MOTION_ZOOM_MAX = 1.015

try:
    from config import PRO_MOTION_FPS
except ImportError:
    PRO_MOTION_FPS = 24

try:
    from config import PRO_MOTION_PRESET
except ImportError:
    PRO_MOTION_PRESET = "veryfast"

try:
    from config import PRO_MOTION_USE_GPU_ENCODER
except ImportError:
    PRO_MOTION_USE_GPU_ENCODER = True

try:
    from config import PRO_MOTION_TIMEOUT_SECONDS
except ImportError:
    PRO_MOTION_TIMEOUT_SECONDS = 300

try:
    from config import PRO_MOTION_TOTAL_TIMEOUT_SECONDS
except ImportError:
    PRO_MOTION_TOTAL_TIMEOUT_SECONDS = 900

try:
    from config import ENABLE_SIMPLE_ALL_SCENE_MOTION
except ImportError:
    ENABLE_SIMPLE_ALL_SCENE_MOTION = False

try:
    from config import SIMPLE_MOTION_PATTERN
except ImportError:
    SIMPLE_MOTION_PATTERN = [
        "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
    ]

try:
    from config import SIMPLE_MOTION_AMOUNT
except ImportError:
    SIMPLE_MOTION_AMOUNT = 0.02

try:
    from config import SIMPLE_MOTION_FPS
except ImportError:
    SIMPLE_MOTION_FPS = 24

try:
    from config import SIMPLE_MOTION_PRESET
except ImportError:
    SIMPLE_MOTION_PRESET = "ultrafast"

try:
    from config import SIMPLE_MOTION_USE_GPU_ENCODER
except ImportError:
    SIMPLE_MOTION_USE_GPU_ENCODER = True

try:
    from config import SIMPLE_MOTION_TIMEOUT_SECONDS
except ImportError:
    SIMPLE_MOTION_TIMEOUT_SECONDS = 180

try:
    from config import SIMPLE_MOTION_TOTAL_TIMEOUT_SECONDS
except ImportError:
    SIMPLE_MOTION_TOTAL_TIMEOUT_SECONDS = 1200

_ENCODER_CHOICE: str | None = None

_DEFAULT_SIMPLE_PATTERN = ["zoom_in", "zoom_out"]

_FFMPEG_XFADE_MAP = {
    "fade": "fade",
    "crossfade": "dissolve",
    "dip_to_black": "fadeblack",
}


def _simple_motion_active() -> bool:
    return bool(ENABLE_SIMPLE_ALL_SCENE_MOTION)


def _professional_motion_active() -> bool:
    if _simple_motion_active():
        return False
    return bool(ENABLE_PROFESSIONAL_MOTION) and str(
        PRO_MOTION_STYLE or "static_plus_push"
    ).lower().strip() in ("static_plus_push", "professional", "cinematic")


def _simple_motion_amount() -> float:
    return max(0.005, min(float(SIMPLE_MOTION_AMOUNT or 0.02), 0.04))


def _simple_zoom_max() -> float:
    return min(1.02, round(1.0 + _simple_motion_amount(), 4))


def _simple_motion_pattern_list() -> list[str]:
    raw = SIMPLE_MOTION_PATTERN
    if not raw:
        return list(_DEFAULT_SIMPLE_PATTERN)
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    else:
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
    valid = set(_DEFAULT_SIMPLE_PATTERN)
    cleaned = [p for p in parts if p in valid]
    return cleaned or list(_DEFAULT_SIMPLE_PATTERN)


def _simple_motion_type(scene_index: int, scene_number: int | None = None) -> str:
    """Odd scenes (1,3,5,...) zoom in; even scenes (2,4,6,...) zoom out."""
    num = int(scene_number) if scene_number is not None else scene_index + 1
    return "zoom_in" if num % 2 == 1 else "zoom_out"


def _effective_motion_fps() -> int:
    if _simple_motion_active():
        return max(12, int(SIMPLE_MOTION_FPS or 24))
    if _professional_motion_active():
        return max(12, int(PRO_MOTION_FPS or 24))
    return max(12, int(KEY_SCENE_MOTION_FPS or 24))


def _effective_zoom_max() -> float:
    if _simple_motion_active():
        return _simple_zoom_max()
    if _professional_motion_active():
        return max(1.005, min(float(PRO_MOTION_ZOOM_MAX or 1.015), 1.02))
    return max(1.01, min(float(KEY_SCENE_MOTION_ZOOM_MAX or 1.03), 1.06))


def _effective_motion_max_scenes() -> int:
    if _professional_motion_active():
        return max(1, min(int(PRO_MOTION_MAX_SCENES or 3), 3))
    return max(1, min(int(KEY_SCENE_MOTION_MAX_SCENES or 3), 3))


def _effective_encode_preset() -> str:
    if _simple_motion_active():
        return str(SIMPLE_MOTION_PRESET or "ultrafast").strip()
    if _professional_motion_active():
        return str(PRO_MOTION_PRESET or "veryfast").strip()
    return str(KEY_SCENE_MOTION_PRESET or "veryfast").strip()


def _effective_use_gpu_encoder() -> bool:
    if _simple_motion_active():
        return bool(SIMPLE_MOTION_USE_GPU_ENCODER)
    if _professional_motion_active():
        return bool(PRO_MOTION_USE_GPU_ENCODER)
    return bool(KEY_SCENE_MOTION_USE_GPU_ENCODER)


def _effective_motion_timeout() -> float:
    if _simple_motion_active():
        return float(SIMPLE_MOTION_TIMEOUT_SECONDS or 180)
    if _professional_motion_active():
        return float(PRO_MOTION_TIMEOUT_SECONDS or 300)
    return float(KEY_SCENE_MOTION_TIMEOUT_SECONDS or 300)


def _effective_motion_total_timeout() -> float:
    if _simple_motion_active():
        return float(SIMPLE_MOTION_TOTAL_TIMEOUT_SECONDS or 1200)
    if _professional_motion_active():
        return float(PRO_MOTION_TOTAL_TIMEOUT_SECONDS or 900)
    return float(KEY_SCENE_MOTION_TOTAL_TIMEOUT_SECONDS or 900)


def _motion_pipeline_enabled() -> bool:
    if _simple_motion_active():
        return True
    if _professional_motion_active():
        return True
    return bool(ENABLE_KEY_SCENE_MOTION or ENABLE_IMAGE_MOTION)

# ============================================


# ============================================
# Local FFmpeg Renderer
# ============================================
# This replaces Creatomate/Supabase assembly.
# It renders:
# images + voiceover + optional music + burned captions
# locally into a 1080x1920 MP4.


def _get_ffmpeg_path() -> str:
    """
    Uses imageio-ffmpeg if installed.
    Falls back to system ffmpeg if available.
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _ffmpeg_cmd(cmd: list[str], *, quiet: bool = True) -> list[str]:
    """Insert quiet FFmpeg flags (errors only) after the executable path."""
    if not cmd:
        return cmd
    out = [cmd[0]]
    if quiet:
        out.extend(["-hide_banner", "-loglevel", "error"])
    out.extend(cmd[1:])
    return out


def _run(cmd: list[str], label: str, timeout_sec: float | None = None, *, quiet: bool = True):
    """Run FFmpeg; suppress routine output, print stderr on failure."""
    print(f"   ▶ {label}")
    run_cmd = _ffmpeg_cmd(cmd, quiet=quiet)

    try:
        result = subprocess.run(
            run_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        raise

    if result.returncode != 0:
        print("\n❌ FFmpeg failed.")
        print("Command:")
        print(" ".join(run_cmd))
        print("\nFFmpeg error:")
        print(result.stderr[-4000:])
        raise RuntimeError(f"FFmpeg failed during: {label}")


def _safe_title(title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", title.strip())
    return cleaned[:50] or "video"


def _parse_volume(volume_value, default: float = 0.10) -> float:
    """
    Converts "10%" or 0.1 into float volume for FFmpeg.
    """
    if volume_value is None:
        return default

    if isinstance(volume_value, (int, float)):
        return max(0.0, float(volume_value))

    value = str(volume_value).strip()

    if value.endswith("%"):
        try:
            return max(0.0, float(value[:-1]) / 100.0)
        except ValueError:
            return default

    try:
        return max(0.0, float(value))
    except ValueError:
        return default


# Obsolete music loading logic removed (handled by modules/music.py)


def _ass_time(seconds: float) -> str:
    """
    Convert seconds to ASS subtitle timestamp H:MM:SS.cc
    """
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))

    if centis >= 100:
        secs += 1
        centis = 0

    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape_ass_text(text: str) -> str:
    """
    Escape text for ASS subtitles.
    """
    text = str(text or "").strip()
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\n", " ")
    return text


def _write_ass_captions(captions: list, output_path: str):
    """
    Write burned-in captions as ASS subtitle file.
    """
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,310,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    for cap in captions:
        start = float(cap.get("start", 0))
        duration = float(cap.get("duration", 0))
        end = float(cap.get("end", start + duration))

        if end <= start:
            end = start + max(duration, 0.25)

        text = _escape_ass_text(cap.get("text", ""))

        if not text:
            continue

        ass += f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}\n"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass)


def _scene_duration(img: dict) -> float:
    start = float(img.get("timestamp_start", 0) or 0)
    end = float(img.get("timestamp_end", 0) or 0)
    return max(0.20, round(end - start, 3))


def _encode_duration(
    base_duration: float,
    scene_index: int,
    total_scenes: int,
) -> float:
    """
    Extend non-final clips when transitions overlap, so joined length matches
    the sum of pipeline scene durations (voiceover/caption alignment).
    """
    if (
        not ENABLE_SCENE_TRANSITIONS
        or total_scenes < 2
        or scene_index >= total_scenes - 1
    ):
        return base_duration

    pad = float(SCENE_TRANSITION_DURATION or 0.25)
    return round(base_duration + pad, 3)


def _pro_motion_kind(
    scene_index: int,
    total_scenes: int,
    scene_number: int,
    key_motion_scenes: set[int],
) -> str | None:
    """
    Professional motion: slow centered push-in on first/last key scenes only.
    Middle key scene stays static. No zoom-out (less stable in zoompan).
    """
    if scene_number not in key_motion_scenes:
        return None
    if scene_index == 0 or scene_index == total_scenes - 1:
        return "push_in"
    return "static"


def _legacy_motion_pattern(scene_index: int, total_scenes: int) -> str:
    if scene_index == 0:
        return "zoom_in"
    if scene_index == total_scenes - 1:
        return "zoom_out"
    return "zoom_in"


def _select_key_motion_scene_numbers(sorted_images: list) -> set[int]:
    """
    Pick 2–3 scene numbers: first, middle turning point, optional last.
    """
    n = len(sorted_images)
    if n == 0:
        return set()

    max_scenes = _effective_motion_max_scenes()
    indices: list[int] = []

    if KEY_SCENE_MOTION_INCLUDE_FIRST:
        indices.append(0)

    if n >= 3:
        mid = n // 2
        if mid not in indices and len(indices) < max_scenes:
            indices.append(mid)
    elif n == 2 and len(indices) < max_scenes:
        if 1 not in indices:
            indices.append(1)

    if KEY_SCENE_MOTION_INCLUDE_LAST and n > 1:
        last = n - 1
        if last not in indices and len(indices) < max_scenes:
            indices.append(last)

    while len(indices) > max_scenes:
        if len(indices) > 1 and indices[-1] != 0:
            indices.pop()
        else:
            break

    scene_numbers = set()
    for idx in indices:
        img = sorted_images[idx]
        scene_numbers.add(int(img.get("scene_number", idx + 1)))
    return scene_numbers


def _motion_enabled_for_scene(
    scene_number: int,
    key_motion_scenes: set[int],
    *,
    scene_index: int = 0,
    total_scenes: int = 1,
) -> bool:
    if _simple_motion_active():
        return True

    if _professional_motion_active():
        kind = _pro_motion_kind(
            scene_index, total_scenes, scene_number, key_motion_scenes
        )
        return kind == "push_in"

    if not ENABLE_KEY_SCENE_MOTION:
        return bool(ENABLE_IMAGE_MOTION)
    if KEY_SCENE_MOTION_ONLY:
        return scene_number in key_motion_scenes
    return bool(ENABLE_IMAGE_MOTION) or scene_number in key_motion_scenes


def _configured_transition_type() -> str:
    """User-facing transition name from config (e.g. crossfade, fade)."""
    chosen = str(SCENE_TRANSITION_TYPE or "crossfade").lower().strip()
    if chosen in _FFMPEG_XFADE_MAP:
        return chosen
    return "crossfade"


def _resolve_transition_type() -> str:
    options = [str(x).lower().strip() for x in (SCENE_TRANSITION_OPTIONS or [])]
    options = [x for x in options if x in _FFMPEG_XFADE_MAP]
    if not options:
        options = ["crossfade"]

    if SCENE_TRANSITION_RANDOMIZE:
        return random.choice(options)

    return _configured_transition_type()


def _ffmpeg_xfade_name(transition_type: str) -> str:
    return _FFMPEG_XFADE_MAP.get(
        str(transition_type or "fade").lower().strip(),
        "fade",
    )


def _static_scene_filter(fps: int) -> str:
    w, h = int(VIDEO_WIDTH), int(VIDEO_HEIGHT)
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},"
        f"setsar=1,"
        f"fps={fps},"
        f"format=yuv420p"
    )


def _simple_zoompan_filter(duration: float, motion_type: str, fps: int) -> str:
    """
    Centered zoom only — linear z(on), fixed trunc x/y, no pan/drift/crop animation.
    """
    w, h = int(VIDEO_WIDTH), int(VIDEO_HEIGHT)
    pw, ph = w // 2, h // 2
    frames = max(24, int(round(max(0.5, float(duration)) * fps)))
    zmax = _simple_zoom_max()
    dz = round(zmax - 1.0, 6)
    center_x = "trunc(iw/2-(iw/zoom/2))"
    center_y = "trunc(ih/2-(ih/zoom/2))"

    if motion_type == "zoom_out":
        zexpr = f"max(1,{zmax}-{dz}*on/{frames})"
    else:
        zexpr = f"min({zmax},1+{dz}*on/{frames})"

    return (
        f"scale={pw}:{ph}:force_original_aspect_ratio=increase,"
        f"crop={pw}:{ph},"
        f"zoompan=z='{zexpr}':x='{center_x}':y='{center_y}':"
        f"d={frames}:s={pw}x{ph}:fps={fps},"
        f"scale={w}:{h}:flags=lanczos,"
        f"setsar=1,"
        f"format=yuv420p"
    )


def _stable_zoompan_filter(duration: float, kind: str, fps: int) -> str:
    """
    Centered zoompan only — no pan/drift, no time-based crop, no x/y animation.
    push_in: z=min(zoom+0.0005,zmax). zoom_out uses slow decrement when requested.
    """
    w, h = int(VIDEO_WIDTH), int(VIDEO_HEIGHT)
    pw, ph = w // 2, h // 2
    frames = max(24, int(round(max(0.5, float(duration)) * fps)))
    zmax = _effective_zoom_max()
    center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"

    if kind in ("push_in", "zoom_in"):
        zexpr = f"min(zoom+0.0005,{zmax})"
    else:
        zexpr = f"if(eq(on,1),{zmax},max(zoom-0.0005,1))"

    return (
        f"scale={pw}:{ph}:force_original_aspect_ratio=increase,"
        f"crop={pw}:{ph},"
        f"zoompan=z='{zexpr}':{center}:d={frames}:s={pw}x{ph}:fps={fps},"
        f"scale={w}:{h}:flags=lanczos,"
        f"setsar=1,"
        f"format=yuv420p"
    )


def _build_motion_filter(
    duration: float,
    scene_index: int,
    total_scenes: int,
    scene_number: int,
    key_motion_scenes: set[int],
    fps: int,
    *,
    simple_motion_type: str | None = None,
) -> tuple[str, str]:
    """Return (vf_filter, log_label)."""
    if _simple_motion_active() and simple_motion_type:
        return (
            _simple_zoompan_filter(duration, simple_motion_type, fps),
            simple_motion_type,
        )

    if _professional_motion_active():
        kind = _pro_motion_kind(
            scene_index, total_scenes, scene_number, key_motion_scenes
        )
        if kind == "push_in":
            return _stable_zoompan_filter(duration, "push_in", fps), "cinematic push-in"
        return _static_scene_filter(fps), "static hold"

    pattern = _legacy_motion_pattern(scene_index, total_scenes)
    label = "cinematic push-in" if pattern == "zoom_in" else "cinematic zoom-out"
    return _stable_zoompan_filter(duration, pattern, fps), label


def _reset_video_encoder_cache() -> None:
    global _ENCODER_CHOICE
    _ENCODER_CHOICE = None


def _resolve_video_encoder(ffmpeg: str) -> tuple[list[str], str]:
    """Pick h264_nvenc when allowed and available, else libx264."""
    global _ENCODER_CHOICE
    if _ENCODER_CHOICE:
        return _ENCODER_CHOICE

    preset = _effective_encode_preset()
    cpu_args = ["-c:v", "libx264", "-preset", preset, "-crf", "20"]

    if not _effective_use_gpu_encoder():
        _ENCODER_CHOICE = (cpu_args, "cpu")
        return _ENCODER_CHOICE

    probe = [
        ffmpeg,
        "-hide_banner",
        "-f", "lavfi",
        "-i", "nullsrc=s=64x64:d=0.1",
        "-c:v", "h264_nvenc",
        "-f", "null",
        "-",
    ]
    try:
        probe_result = subprocess.run(
            probe,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if probe_result.returncode == 0:
            nv_preset = "p1" if preset in ("ultrafast", "veryfast", "superfast") else "p4"
            gpu_args = [
                "-c:v", "h264_nvenc",
                "-preset", nv_preset,
                "-rc", "vbr",
                "-cq", "21",
            ]
            _ENCODER_CHOICE = (gpu_args, "gpu")
            return _ENCODER_CHOICE
    except (subprocess.TimeoutExpired, OSError):
        pass

    _ENCODER_CHOICE = (cpu_args, "cpu")
    return _ENCODER_CHOICE


def _scene_encode_cmd(
    ffmpeg: str,
    image_path: str,
    duration: float,
    output_path: str,
    vf: str,
    encoder_args: list[str],
    *,
    use_threads: bool = False,
) -> list[str]:
    cmd = [ffmpeg, "-y"]
    if use_threads:
        cmd.extend(["-threads", "0"])
    cmd.extend([
        "-loop", "1",
        "-t", str(duration),
        "-i", image_path,
        "-vf", vf,
        "-an",
        *encoder_args,
        "-pix_fmt", "yuv420p",
        output_path,
    ])
    return cmd


def _motion_timeout_seconds(motion_budget: dict | None) -> float:
    per_scene = _effective_motion_timeout()
    if IMAGE_MOTION_SCENE_TIMEOUT_SEC is not None:
        try:
            legacy = float(IMAGE_MOTION_SCENE_TIMEOUT_SEC)
            if legacy > per_scene:
                per_scene = legacy
        except (TypeError, ValueError):
            pass

    if not motion_budget:
        return per_scene

    total_limit = _effective_motion_total_timeout()
    remaining = total_limit - float(motion_budget.get("elapsed", 0.0))
    return max(1.0, min(per_scene, remaining))


def _motion_budget_exhausted(motion_budget: dict | None) -> bool:
    if not motion_budget:
        return False
    total_limit = _effective_motion_total_timeout()
    return float(motion_budget.get("elapsed", 0.0)) >= total_limit


def _render_static_scene_clip(
    ffmpeg: str,
    image_path: str,
    duration: float,
    output_path: str,
    encoder_args: list[str],
    fps: int,
):
    cmd = _scene_encode_cmd(
        ffmpeg,
        image_path,
        duration,
        output_path,
        _static_scene_filter(fps),
        encoder_args,
    )
    _run(cmd, f"Static scene {Path(image_path).name}")


def _render_scene_clip(
    ffmpeg: str,
    image_path: str,
    duration: float,
    output_path: str,
    *,
    scene_number: int = 1,
    scene_index: int = 0,
    total_scenes: int = 1,
    apply_motion: bool = False,
    key_motion_scenes: set[int] | None = None,
    encoder_args: list[str] | None = None,
    fps: int = 24,
    motion_budget: dict | None = None,
    simple_motion_type: str | None = None,
):
    """
    Render one still image into a vertical 1080x1920 clip.
    """
    if encoder_args is None:
        encoder_args, _ = _resolve_video_encoder(ffmpeg)

    key_motion_scenes = key_motion_scenes or set()

    if _simple_motion_active():
        if _motion_budget_exhausted(motion_budget):
            print(f"   Scene {scene_number}: static fallback (motion budget)")
            _render_static_scene_clip(
                ffmpeg, image_path, duration, output_path, encoder_args, fps
            )
            return

        motion_type = simple_motion_type or _simple_motion_type(
            scene_index, scene_number
        )
        timeout_sec = _motion_timeout_seconds(motion_budget)
        vf = _simple_zoompan_filter(duration, motion_type, fps)
        print(f"   Scene {scene_number}: {motion_type}")

        started = time.time()
        try:
            cmd = _scene_encode_cmd(
                ffmpeg,
                image_path,
                duration,
                output_path,
                vf,
                encoder_args,
                use_threads=True,
            )
            _run(
                cmd,
                f"Motion scene {Path(image_path).name}",
                timeout_sec=timeout_sec,
            )
            elapsed = time.time() - started
            if motion_budget is not None:
                motion_budget["elapsed"] = float(motion_budget.get("elapsed", 0.0)) + elapsed
            print(f"   Finished scene {scene_number} in {elapsed:.1f}s")
        except (subprocess.TimeoutExpired, Exception):
            if motion_budget is not None:
                motion_budget["elapsed"] = float(
                    motion_budget.get("elapsed", 0.0)
                ) + (time.time() - started)
            print(f"   Scene {scene_number}: static fallback")
            _render_static_scene_clip(
                ffmpeg, image_path, duration, output_path, encoder_args, fps
            )
        return

    if _professional_motion_active():
        kind = _pro_motion_kind(
            scene_index, total_scenes, scene_number, key_motion_scenes
        )
        if not apply_motion or kind in (None, "static"):
            print(f"   Scene {scene_number}: static hold")
            _render_static_scene_clip(
                ffmpeg, image_path, duration, output_path, encoder_args, fps
            )
            return
    elif not apply_motion:
        print(f"   Scene {scene_number}: static hold")
        _render_static_scene_clip(
            ffmpeg, image_path, duration, output_path, encoder_args, fps
        )
        return

    if _motion_budget_exhausted(motion_budget):
        total_limit = _effective_motion_total_timeout()
        print(
            f"   Motion total budget ({total_limit:.0f}s) reached; "
            f"scene {scene_number}: static hold"
        )
        _render_static_scene_clip(
            ffmpeg, image_path, duration, output_path, encoder_args, fps
        )
        return

    timeout_sec = _motion_timeout_seconds(motion_budget)
    vf, label = _build_motion_filter(
        duration,
        scene_index,
        total_scenes,
        scene_number,
        key_motion_scenes,
        fps,
    )
    print(f"   Scene {scene_number}: {label}")

    started = time.time()
    try:
        cmd = _scene_encode_cmd(
            ffmpeg,
            image_path,
            duration,
            output_path,
            vf,
            encoder_args,
            use_threads=True,
        )
        _run(
            cmd,
            f"Motion scene {Path(image_path).name}",
            timeout_sec=timeout_sec,
        )
        elapsed = time.time() - started
        if motion_budget is not None:
            motion_budget["elapsed"] = float(motion_budget.get("elapsed", 0.0)) + elapsed
        print(f"   Finished motion scene {scene_number} in {elapsed:.1f}s")
    except subprocess.TimeoutExpired:
        elapsed = time.time() - started
        if motion_budget is not None:
            motion_budget["elapsed"] = float(motion_budget.get("elapsed", 0.0)) + elapsed
        print(f"   Scene {scene_number}: static fallback")
        _render_static_scene_clip(
            ffmpeg, image_path, duration, output_path, encoder_args, fps
        )
    except Exception as exc:
        elapsed = time.time() - started
        if motion_budget is not None and elapsed > 0:
            motion_budget["elapsed"] = float(motion_budget.get("elapsed", 0.0)) + elapsed
        print(f"   Scene {scene_number}: static fallback")
        _render_static_scene_clip(
            ffmpeg, image_path, duration, output_path, encoder_args, fps
        )


def _concat_clips_copy(ffmpeg: str, clip_paths: list[str], output_path: str):
    """Fast concat without transitions (stream copy)."""
    concat_file = Path(output_path).with_suffix(".txt")

    with open(concat_file, "w", encoding="utf-8") as f:
        for clip in clip_paths:
            safe_path = Path(clip).resolve().as_posix().replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")

    cmd = [
        ffmpeg,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        output_path,
    ]

    _run(cmd, "Concatenate scene clips (no transitions)")


def _concat_clips_with_transitions(
    ffmpeg: str,
    clip_paths: list[str],
    clip_durations: list[float],
    output_path: str,
    transition_type: str,
    transition_duration: float,
    encoder_args: list[str],
):
    """Join scene clips with FFmpeg xfade transitions."""
    n = len(clip_paths)
    if n == 1:
        cmd = [
            ffmpeg,
            "-y",
            "-i", clip_paths[0],
            "-c", "copy",
            output_path,
        ]
        _run(cmd, "Single scene (no transitions)")
        return

    trans_dur = max(0.05, min(float(transition_duration), 1.0))
    xfade_name = _ffmpeg_xfade_name(transition_type)

    cmd = [ffmpeg, "-y"]
    for clip in clip_paths:
        cmd.extend(["-i", clip])

    filter_parts = []
    prev_label = "[0:v]"
    for i in range(1, n):
        offset = sum(clip_durations[:i]) - i * trans_dur
        offset = max(0.0, offset)
        out_label = "[vout]" if i == n - 1 else f"[vx{i}]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition={xfade_name}:"
            f"duration={trans_dur:.3f}:offset={offset:.3f}{out_label}"
        )
        prev_label = out_label

    filter_complex = ";".join(filter_parts)
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-an",
        *encoder_args,
        "-pix_fmt", "yuv420p",
        output_path,
    ])

    _run(
        cmd,
        f"Join scenes with {transition_type} transitions ({trans_dur:.2f}s)",
    )


def _assemble_scene_clips(
    ffmpeg: str,
    clip_paths: list[str],
    clip_durations: list[float],
    output_path: str,
    encoder_args: list[str],
):
    if len(clip_paths) < 2 or not ENABLE_SCENE_TRANSITIONS:
        _concat_clips_copy(ffmpeg, clip_paths, output_path)
        return

    trans_type = _resolve_transition_type()
    trans_dur = float(SCENE_TRANSITION_DURATION or 0.25)
    _concat_clips_with_transitions(
        ffmpeg,
        clip_paths,
        clip_durations,
        output_path,
        trans_type,
        trans_dur,
        encoder_args,
    )


def _burn_captions_and_mix_audio(
    ffmpeg: str,
    background_video: str,
    audio_path: str,
    ass_path: str,
    output_path: str,
    duration: float,
    music_path: str | None = None,
    music_volume: float = 0.0,
    sfx_selections: list | None = None,
):
    """
    Burn captions and mix voiceover, background music, and optional sound effects.
    """
    ass_filter_path = Path(ass_path).as_posix()
    subtitle_filter = f"subtitles='{ass_filter_path}'"

    # Base commands
    cmd = [
        ffmpeg,
        "-y",
        "-i", background_video,
        "-i", audio_path,
    ]

    # Video filter chain
    vf_expr = f"[0:v]{subtitle_filter}[v]"

    # Audio inputs list for amix
    audio_inputs = "[a0]"
    filter_lines = [
        f"[1:a]volume=1.0,atrim=0:{duration},asetpts=PTS-STARTPTS[a0]"
    ]

    next_input_idx = 2

    # Add looping background music if selected
    if music_path and os.path.isfile(music_path):
        cmd.extend(["-stream_loop", "-1", "-i", music_path])
        filter_lines.append(f"[{next_input_idx}:a]volume={music_volume},atrim=0:{duration},asetpts=PTS-STARTPTS[a1]")
        audio_inputs += "[a1]"
        next_input_idx += 1

    # Add optional sound effects (skip individual failures)
    valid_sfxs = []
    if sfx_selections:
        for sfx in sfx_selections:
            sfx_path = sfx.get("path")
            if not sfx_path or not os.path.isfile(sfx_path):
                print(f"   ⚠️  SFX missing, skipping: {sfx.get('filename', sfx_path)}")
                continue
            try:
                if os.path.getsize(sfx_path) < 256:
                    print(f"   ⚠️  SFX file too small, skipping: {sfx_path}")
                    continue
                cmd.extend(["-i", sfx_path])
                valid_sfxs.append(sfx)
            except OSError as exc:
                print(f"   ⚠️  SFX skipped ({exc}): {sfx_path}")

    for idx, sfx in enumerate(valid_sfxs):
        input_idx = next_input_idx + idx
        delay_ms = int(sfx["timestamp"] * 1000)
        sfx_label = f"asfx{idx}"
        filter_lines.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume={sfx['volume']},atrim=0:{duration},asetpts=PTS-STARTPTS[{sfx_label}]")
        audio_inputs += f"[{sfx_label}]"

    # Mix audio streams
    num_inputs = 1 + (1 if music_path and os.path.isfile(music_path) else 0) + len(valid_sfxs)
    filter_lines.append(f"{audio_inputs}amix=inputs={num_inputs}:duration=first:dropout_transition=2[aout]")

    # Build the full filter complex
    filter_complex_expr = ";".join([vf_expr] + filter_lines)

    cmd.extend([
        "-filter_complex", filter_complex_expr,
        "-map", "[v]",
        "-map", "[aout]",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ])

    _run(cmd, "Burn captions and mix audio")
    if valid_sfxs:
        print("   SFX mixed into final audio")
    elif sfx_selections:
        print("   No SFX mixed (files missing or skipped)")


def assemble_video(
    script: dict,
    image_paths: list,
    audio_path: str,
    captions: list,
    prefix: str,
    thumbnail_path: str | None = None,
    video_duration: float | None = None,
) -> str:
    """
    Local assembly replacement.

    Same function signature as the old Creatomate version,
    so main.py does not need to change.
    """
    _ = thumbnail_path

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    if not image_paths:
        raise ValueError("No image paths supplied for assembly")

    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Voiceover audio not found: {audio_path}")

    ffmpeg = _get_ffmpeg_path()

    render_duration = float(video_duration or 0)
    if render_duration <= 0:
        if captions:
            render_duration = max(float(c.get("end", 0)) for c in captions) + 0.4
        else:
            render_duration = 75.0

    render_duration = round(render_duration, 2)

    print("\n🎬 Assembling video locally...")
    print(f"   Resolution: {VIDEO_WIDTH}x{VIDEO_HEIGHT}")
    print(f"   Duration: {render_duration}s")
    print("   Renderer: local FFmpeg")
    print("   No Creatomate credits used.")
    print("   No Supabase uploads used.")

    work_dir = Path(TEMP_DIR) / f"local_render_{prefix.strip('_')}"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Sort images by scene number
    sorted_images = sorted(image_paths, key=lambda x: int(x.get("scene_number", 0)))

    _reset_video_encoder_cache()

    # 1. Render each image into a small video clip
    clip_paths = []
    clip_durations = []
    total_scenes = len(sorted_images)
    motion_fps = _effective_motion_fps()
    zoom_max = _effective_zoom_max()

    key_motion_scenes: set[int] = set()
    if _simple_motion_active():
        print("   Simple all-scene motion enabled")
        print("   Pattern: odd scenes zoom_in, even scenes zoom_out (centered)")
        print(f"   Amount: {_simple_motion_amount():.3f} (max zoom {zoom_max})")
    elif _motion_pipeline_enabled():
        key_motion_scenes = _select_key_motion_scene_numbers(sorted_images)
        if _professional_motion_active():
            print("   Professional motion enabled")
            print(
                f"   Editor style: static holds + crossfades; "
                f"push/zoom on key scenes only (max zoom {zoom_max})"
            )
            print(f"   Key scene numbers: {sorted(key_motion_scenes)}")
        elif ENABLE_KEY_SCENE_MOTION:
            print("   Key-scene motion enabled")
            print(f"   Motion scenes selected: {sorted(key_motion_scenes)}")
        elif ENABLE_IMAGE_MOTION:
            print("   Legacy full-video motion enabled (all scenes)")
            key_motion_scenes = {
                int(img.get("scene_number", i + 1))
                for i, img in enumerate(sorted_images)
            }
    else:
        print("   Image motion: disabled (all scenes static)")

    encoder_args, enc_kind = _resolve_video_encoder(ffmpeg)
    motion_budget = {"elapsed": 0.0}

    if _motion_pipeline_enabled():
        print(f"   Motion render: {motion_fps} fps, zoom max {zoom_max}")
        print(
            f"   Motion limits: {_effective_motion_timeout():.0f}s per scene, "
            f"{_effective_motion_total_timeout():.0f}s total"
        )
        if enc_kind == "gpu":
            print("   Scene encoder: h264_nvenc")
        else:
            print("   Scene encoder: libx264")

    if ENABLE_SCENE_TRANSITIONS and total_scenes >= 2:
        trans_type = _configured_transition_type()
        ffmpeg_trans = _ffmpeg_xfade_name(trans_type)
        if SCENE_TRANSITION_RANDOMIZE:
            trans_type = _resolve_transition_type()
            ffmpeg_trans = _ffmpeg_xfade_name(trans_type)
            print(f"   Scene transitions enabled: random ({trans_type})")
        elif trans_type == "crossfade":
            print("   Crossfade transitions enabled")
        else:
            print(f"   Scene transitions enabled: {trans_type}")
        print(f"   Transition config: SCENE_TRANSITION_TYPE={trans_type} (ffmpeg={ffmpeg_trans})")
    else:
        print("   Scene transitions: disabled")

    for scene_idx, img in enumerate(sorted_images):
        scene_number = int(img.get("scene_number", len(clip_paths) + 1))
        image_path = str(img["path"])

        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Scene image not found: {image_path}")

        base_duration = _scene_duration(img)
        encode_duration = _encode_duration(base_duration, scene_idx, total_scenes)
        clip_path = str(work_dir / f"scene_{scene_number:02d}.mp4")
        apply_motion = _motion_enabled_for_scene(
            scene_number,
            key_motion_scenes,
            scene_index=scene_idx,
            total_scenes=total_scenes,
        )
        simple_type = (
            _simple_motion_type(scene_idx, scene_number)
            if _simple_motion_active()
            else None
        )

        _render_scene_clip(
            ffmpeg=ffmpeg,
            image_path=image_path,
            duration=encode_duration,
            output_path=clip_path,
            scene_number=scene_number,
            scene_index=scene_idx,
            total_scenes=total_scenes,
            apply_motion=apply_motion,
            key_motion_scenes=key_motion_scenes,
            encoder_args=encoder_args,
            fps=motion_fps,
            motion_budget=motion_budget,
            simple_motion_type=simple_type,
        )

        clip_paths.append(clip_path)
        clip_durations.append(encode_duration)

    # 2. Join scene clips (xfade transitions or fast concat)
    background_video = str(work_dir / "background_video.mp4")
    _assemble_scene_clips(
        ffmpeg,
        clip_paths,
        clip_durations,
        background_video,
        encoder_args,
    )

    # 3. Write ASS captions
    ass_path = str(work_dir / f"{prefix}captions.ass")
    _write_ass_captions(captions, ass_path)

    # 4. Choose background music and sound effects intelligently
    from modules.music import (
        choose_music_track,
        choose_sfx_for_video,
        log_sfx_status,
        save_music_selection_metadata,
    )

    music_path, music_volume = choose_music_track(script)
    log_sfx_status()
    sfx_selections = choose_sfx_for_video(script, topic=None, image_paths=sorted_images)

    # Save selection details
    save_music_selection_metadata(prefix, music_path, music_volume, sfx_selections)

    # 5. Burn captions + mix voice/music/SFX
    safe_title = _safe_title(script.get("title", f"{prefix.strip('_')}"))
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(
        OUTPUT_DIR,
        f"{prefix}{safe_title}_{timestamp}.mp4",
    )

    print("   Preparing SFX mix")
    if sfx_selections:
        for sfx in sfx_selections:
            fn = sfx.get("filename", Path(sfx.get("path", "")).name)
            ts = float(sfx.get("timestamp", 0))
            print(f"   SFX selected: {fn} @ {ts:.1f}s")
    else:
        print("   SFX selected: none")

    _burn_captions_and_mix_audio(
        ffmpeg=ffmpeg,
        background_video=background_video,
        audio_path=audio_path,
        ass_path=ass_path,
        output_path=output_path,
        duration=render_duration,
        music_path=music_path,
        music_volume=music_volume,
        sfx_selections=sfx_selections,
    )

    print(f"✅ Local video saved: {output_path}")
    return output_path