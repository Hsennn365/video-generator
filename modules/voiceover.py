import os
import re
import subprocess
import time
from pathlib import Path

import requests

from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, VOICE_SETTINGS, TEMP_DIR

try:
    from config import ELEVENLABS_MODEL_ID
except ImportError:
    ELEVENLABS_MODEL_ID = "eleven_turbo_v2_5"


VOICE_MARKER_RE = re.compile(r"\[\[(pause|voice):([^\]]+)\]\]", re.IGNORECASE)


def _get_ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _run_ffmpeg(cmd: list[str], label: str) -> None:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        print(f"\n❌ FFmpeg failed during: {label}")
        print(result.stderr[-3000:])
        raise RuntimeError(f"FFmpeg failed during: {label}")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _voice_settings_for_mode(mode: str) -> dict:
    """
    ElevenLabs does not guarantee true acting-direction changes from settings alone,
    but splitting the narration and changing stability/style/speed gives more variation
    than one flat generation.
    """
    base = dict(VOICE_SETTINGS)
    mode = (mode or "default").lower().strip()

    stability = float(base.get("stability", 0.42))
    style = float(base.get("style", 0.70))
    speed = float(base.get("speed", 0.91))

    if mode == "low":
        base["stability"] = _clamp(stability + 0.10, 0.0, 1.0)
        base["style"] = _clamp(style - 0.10, 0.0, 1.0)
        base["speed"] = _clamp(speed - 0.03, 0.7, 1.2)

    elif mode == "tense":
        base["stability"] = _clamp(stability - 0.08, 0.0, 1.0)
        base["style"] = _clamp(style + 0.12, 0.0, 1.0)
        base["speed"] = _clamp(speed - 0.01, 0.7, 1.2)

    elif mode == "urgent":
        base["stability"] = _clamp(stability - 0.12, 0.0, 1.0)
        base["style"] = _clamp(style + 0.16, 0.0, 1.0)
        base["speed"] = _clamp(speed + 0.04, 0.7, 1.2)

    else:
        mode = "default"

    return base


def _parse_narration(narration: str) -> list[dict]:
    """
    Convert narration into a sequence of text/voice/pause parts.

    Example:
    "Rome feared one city. [[pause:0.6]][[voice:tense]] Carthage."
    """
    parts: list[dict] = []
    current_voice = "default"
    cursor = 0

    for match in VOICE_MARKER_RE.finditer(narration):
        before = _clean_text(narration[cursor:match.start()])
        if before:
            parts.append({
                "type": "text",
                "text": before,
                "voice": current_voice,
            })

        marker_type = match.group(1).lower()
        marker_value = match.group(2).strip()

        if marker_type == "pause":
            try:
                duration = float(marker_value)
            except ValueError:
                duration = 0.4

            duration = _clamp(duration, 0.15, 2.0)
            parts.append({
                "type": "pause",
                "duration": duration,
            })

        elif marker_type == "voice":
            marker_value = marker_value.lower().strip()
            if marker_value in {"default", "low", "tense", "urgent"}:
                current_voice = marker_value

        cursor = match.end()

    tail = _clean_text(narration[cursor:])
    if tail:
        parts.append({
            "type": "text",
            "text": tail,
            "voice": current_voice,
        })

    return parts


def _generate_tts_segment(text: str, output_path: str, voice_mode: str) -> str:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY,
    }

    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": _voice_settings_for_mode(voice_mode),
    }

    response = requests.post(url, json=payload, headers=headers, timeout=300)

    if response.status_code != 200:
        raise Exception(f"ElevenLabs API error {response.status_code}: {response.text}")

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


def _generate_silence_mp3(duration: float, output_path: str) -> str:
    ffmpeg = _get_ffmpeg_path()

    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duration),
        "-q:a", "9",
        "-acodec", "libmp3lame",
        output_path,
    ]

    _run_ffmpeg(cmd, f"generate {duration}s silence")
    return output_path


def _concat_mp3(parts: list[str], output_path: str) -> None:
    ffmpeg = _get_ffmpeg_path()
    list_path = str(Path(output_path).with_suffix(".concat.txt"))

    with open(list_path, "w", encoding="utf-8") as f:
        for p in parts:
            safe = Path(p).resolve().as_posix().replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        ffmpeg,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c:a", "libmp3lame",
        "-q:a", "4",
        output_path,
    ]

    _run_ffmpeg(cmd, "concatenate voiceover segments")


def _strip_markers(text: str) -> str:
    return VOICE_MARKER_RE.sub("", text)


def generate_voiceover(narration: str, output_filename: str) -> str:
    """
    Converts narration to MP3 using ElevenLabs.

    Supports:
    [[pause:0.6]]
    [[voice:default]]
    [[voice:low]]
    [[voice:tense]]
    [[voice:urgent]]

    The markers are not spoken.
    They are used to split the generation and insert real silence.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)
    output_path = os.path.join(TEMP_DIR, output_filename)

    parts = _parse_narration(narration)

    # If no markers exist, use the original cheaper/simpler single-call behavior.
    if len(parts) == 1 and parts[0]["type"] == "text":
        _generate_tts_segment(
            text=_strip_markers(narration).strip(),
            output_path=output_path,
            voice_mode="default",
        )
        print(f"✅ Voiceover generated: {output_path}")
        return output_path

    stem = Path(output_filename).stem
    segment_dir = Path(TEMP_DIR) / f"{stem}_voice_segments"
    segment_dir.mkdir(parents=True, exist_ok=True)

    audio_parts: list[str] = []
    text_count = 0
    pause_count = 0

    print("🎙️  Generating voiceover with real pauses/voice sections...")

    for index, part in enumerate(parts, start=1):
        if part["type"] == "text":
            text_count += 1
            voice_mode = part.get("voice", "default")
            segment_path = str(segment_dir / f"segment_{index:03d}_{voice_mode}.mp3")

            print(f"   🎧 Segment {text_count}: voice={voice_mode}, words={len(part['text'].split())}")

            _generate_tts_segment(
                text=part["text"],
                output_path=segment_path,
                voice_mode=voice_mode,
            )

            audio_parts.append(segment_path)
            time.sleep(0.25)

        elif part["type"] == "pause":
            pause_count += 1
            duration = float(part["duration"])
            silence_path = str(segment_dir / f"pause_{index:03d}_{duration:.2f}s.mp3")

            print(f"   🤫 Pause {pause_count}: {duration:.2f}s")

            _generate_silence_mp3(duration, silence_path)
            audio_parts.append(silence_path)

    if not audio_parts:
        raise Exception("No voiceover audio parts were generated.")

    _concat_mp3(audio_parts, output_path)

    print(f"✅ Voiceover generated with {text_count} spoken segments and {pause_count} pauses: {output_path}")
    return output_path


def get_available_voices() -> list:
    """Fetch all available voices from ElevenLabs — useful for finding the best voice."""
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}

    response = requests.get(url, headers=headers, timeout=120)

    if response.status_code != 200:
        raise Exception(f"ElevenLabs API error: {response.text}")

    voices = response.json()["voices"]

    print("\n🎙️ Available ElevenLabs Voices:")
    for v in voices:
        print(f"  - {v['name']} | ID: {v['voice_id']} | Labels: {v.get('labels', {})}")

    return voices