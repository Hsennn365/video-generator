import json
import os
import re
import subprocess
import time
from pathlib import Path

from config import OUTPUT_DIR, TEMP_DIR, VIDEO_HEIGHT, VIDEO_WIDTH

try:
    from config import MUSIC_DIR, MUSIC_LIBRARY_FILE
except ImportError:
    MUSIC_DIR = "music"
    MUSIC_LIBRARY_FILE = "music/music_library.json"


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


def _run(cmd: list[str], label: str):
    """Run an ffmpeg command and show useful errors."""
    print(f"   ▶ {label}")

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        print("\n❌ FFmpeg failed.")
        print("Command:")
        print(" ".join(cmd))
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


def _load_music_library() -> list:
    path = Path(MUSIC_LIBRARY_FILE)

    if not path.is_file():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"   ⚠️ Could not read music library: {e}")
        return []


def _choose_local_music() -> tuple[str | None, float]:
    """
    Uses first valid local MP3 from music/music_library.json.

    Example:
    [
      {
        "file": "dark_cinematic_01.mp3",
        "volume": "8%"
      }
    ]
    """
    library = _load_music_library()

    for item in library:
        if not isinstance(item, dict):
            continue

        file_name = item.get("file")
        if not file_name:
            continue

        music_path = Path(MUSIC_DIR) / file_name

        if music_path.is_file() and music_path.suffix.lower() == ".mp3":
            volume = _parse_volume(item.get("volume", "10%"), default=0.10)
            print(f"🎵 Local music selected: {music_path} at volume {volume:.2f}")
            return str(music_path), volume

    print("🎵 No local music found. Rendering voiceover only.")
    return None, 0.0


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


def _render_scene_clip(
    ffmpeg: str,
    image_path: str,
    duration: float,
    output_path: str,
):
    """
    Render one still image into a vertical video clip.
    Uses scale+crop to force 1080x1920.
    """
    vf = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        f"setsar=1,"
        f"fps=30,"
        f"format=yuv420p"
    )

    cmd = [
        ffmpeg,
        "-y",
        "-loop", "1",
        "-t", str(duration),
        "-i", image_path,
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        output_path,
    ]

    _run(cmd, f"Render scene clip {Path(image_path).name}")


def _concat_clips(ffmpeg: str, clip_paths: list[str], output_path: str):
    """
    Concatenate rendered scene clips.
    """
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

    _run(cmd, "Concatenate scene clips")


def _burn_captions_and_mix_audio(
    ffmpeg: str,
    background_video: str,
    audio_path: str,
    ass_path: str,
    output_path: str,
    duration: float,
):
    """
    Burn captions and add voiceover + optional local music.
    """
    music_path, music_volume = _choose_local_music()

    ass_filter_path = Path(ass_path).as_posix()
    subtitle_filter = f"subtitles='{ass_filter_path}'"

    if music_path:
        cmd = [
            ffmpeg,
            "-y",
            "-i", background_video,
            "-i", audio_path,
            "-stream_loop", "-1",
            "-i", music_path,
            "-filter_complex",
            (
                f"[0:v]{subtitle_filter}[v];"
                f"[1:a]volume=1.0,atrim=0:{duration},asetpts=PTS-STARTPTS[a0];"
                f"[2:a]volume={music_volume},atrim=0:{duration},asetpts=PTS-STARTPTS[a1];"
                f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            ),
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
        ]
    else:
        cmd = [
            ffmpeg,
            "-y",
            "-i", background_video,
            "-i", audio_path,
            "-vf", subtitle_filter,
            "-map", "0:v",
            "-map", "1:a",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]

    _run(cmd, "Burn captions and mix audio")


def assemble_video(
    script: dict,
    image_paths: list,
    audio_path: str,
    captions: list,
    topic_id: str,
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

    work_dir = Path(TEMP_DIR) / f"local_render_topic_{topic_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Sort images by scene number
    sorted_images = sorted(image_paths, key=lambda x: int(x.get("scene_number", 0)))

    # 1. Render each image into a small video clip
    clip_paths = []

    for img in sorted_images:
        scene_number = int(img.get("scene_number", len(clip_paths) + 1))
        image_path = str(img["path"])

        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Scene image not found: {image_path}")

        duration = _scene_duration(img)
        clip_path = str(work_dir / f"scene_{scene_number:02d}.mp4")

        _render_scene_clip(
            ffmpeg=ffmpeg,
            image_path=image_path,
            duration=duration,
            output_path=clip_path,
        )

        clip_paths.append(clip_path)

    # 2. Concatenate scene clips
    background_video = str(work_dir / "background_video.mp4")
    _concat_clips(ffmpeg, clip_paths, background_video)

    # 3. Write ASS captions
    ass_path = str(work_dir / f"topic_{topic_id}_captions.ass")
    _write_ass_captions(captions, ass_path)

    # 4. Burn captions + mix voice/music
    safe_title = _safe_title(script.get("title", f"topic_{topic_id}"))
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(
        OUTPUT_DIR,
        f"topic_{topic_id}_{safe_title}_{timestamp}.mp4",
    )

    _burn_captions_and_mix_audio(
        ffmpeg=ffmpeg,
        background_video=background_video,
        audio_path=audio_path,
        ass_path=ass_path,
        output_path=output_path,
        duration=render_duration,
    )

    print(f"✅ Local video saved: {output_path}")
    return output_path