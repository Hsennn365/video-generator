"""Offline, no-credential tests for opt-in motion clips."""
import json
import tempfile
import unittest
from pathlib import Path

from modules.motion_clips import load_motion_clips, render_motion_clip


class MotionClipsTests(unittest.TestCase):
    def test_missing_manifest_uses_original_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_motion_clips("topic_1_", folder), {})

    def test_manifest_loads_local_video(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "test.mp4"
            video.write_bytes(b"test" * 2000)
            manifest = Path(folder) / "topic_1_motion_clips.json"
            manifest.write_text(json.dumps({"scenes": [
                {"scene_number": 2, "path": str(video)}
            ]}), encoding="utf-8")
            result = load_motion_clips("topic_1_", folder)
            self.assertEqual(result, {2: str(video.resolve())})

    def test_duplicate_scene_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "test.mp4"
            video.write_bytes(b"test" * 2000)
            (Path(folder) / "topic_1_motion_clips.json").write_text(
                json.dumps({"scenes": [
                    {"scene_number": 2, "path": str(video)},
                    {"scene_number": 2, "path": str(video)},
                ]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_motion_clips("topic_1_", folder)

    def test_motion_command_keeps_own_audio(self):
        captured = []
        def runner(cmd, title):
            captured.append((cmd, title))
        render_motion_clip("ffmpeg", "clip.mp4", "normalized.mp4",
                           5, 24, 540, 960, ["-c:v", "libx264"], runner)
        self.assertEqual(len(captured), 1)
        cmd, title = captured[0]
        self.assertIn("-an", cmd)
        self.assertIn("-vf", cmd)
        self.assertTrue(any("tpad=" in x for x in cmd))
        self.assertEqual(cmd[-1], "normalized.mp4")

    def test_bad_duration_rejected_without_ffmpeg_call(self):
        def runner(*args): self.fail("Should not render")
        with self.assertRaises(ValueError):
            render_motion_clip("ffmpeg", "clip.mp4", "normalized.mp4",
                               -1, 24, 540, 960, ["-c:v", "libx264"], runner)


if __name__ == "__main__":
    unittest.main()
