"""Two-pass linear loudness on the finished edit (growth-lab S3 audio_norm, target -14 LUFS, audit 2026-10-21 #3).
Run: py -m unittest discover -s tests -v. The ffmpeg test makes a 3 s tone clip (no network)."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audio_norm  # noqa: E402
from spacepipe import render  # noqa: E402
from spacepipe.lib import FFMPEG, FFPROBE  # noqa: E402

PLAN = {"music": {"start": 12.5, "file": "music/x.mp3"}}


def lufs(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


class AudioFilter(unittest.TestCase):
    def test_single_pass_loudnorm_is_gone(self):
        af = render.audio_filter(PLAN, 30.0)
        self.assertNotIn("loudnorm", af)    # the two-pass linear normalise runs on the finished file instead

    def test_trim_fades_and_48k_are_kept(self):
        af = render.audio_filter(PLAN, 30.0)
        self.assertIn("atrim=start=12.50:end=42.50", af)
        self.assertIn("afade=t=out:st=28.20:d=1.8", af)
        self.assertTrue(af.endswith("aresample=48000"))

    def test_target_is_minus_14(self):
        self.assertEqual(render.LOUDNESS_TARGET, -14)


class NormalizeFinal(unittest.TestCase):
    def test_calls_two_pass_with_target_and_keeps_video(self):
        with tempfile.TemporaryDirectory() as d:
            final = Path(d) / "video.mp4"
            final.write_bytes(b"x")

            def fake(src, dst, **kw):
                Path(dst).write_bytes(b"normalised")
                return {"output_i": -14.0, "normalization_type": "linear", "input_i": -20.0, "gain_db": 6.0, "limited": False}
            with mock.patch.object(audio_norm, "normalize", side_effect=fake) as n:
                res = render.normalize_audio(str(final), silent=False)
            kw = n.call_args.kwargs
            self.assertEqual(kw["target"], -14)
            self.assertTrue(kw["keep_video"])
            self.assertEqual(final.read_bytes(), b"normalised")
            self.assertEqual(res["normalization_type"], "linear")
            self.assertEqual(list(Path(d).iterdir()), [final])     # temp file moved into place

    def test_silent_edit_is_not_touched(self):
        with mock.patch.object(audio_norm, "normalize") as n:
            self.assertIsNone(render.normalize_audio("whatever.mp4", silent=True))
        n.assert_not_called()

    def test_failure_keeps_the_unnormalised_mix(self):
        with tempfile.TemporaryDirectory() as d:
            final = Path(d) / "video.mp4"
            final.write_bytes(b"raw mix")
            with mock.patch.object(audio_norm, "normalize", side_effect=RuntimeError("ffmpeg failed")):
                self.assertIsNone(render.normalize_audio(str(final), silent=False))
            self.assertEqual(final.read_bytes(), b"raw mix")      # qc's -17..-11 window still guards it
            self.assertEqual(list(Path(d).iterdir()), [final])

    def test_assemble_normalises_after_the_encode(self):
        src = Path(render.__file__).read_text(encoding="utf-8")
        body = src[src.index("def assemble"):src.index("def render(")]
        self.assertLess(body.index("run(["), body.index("normalize_audio(final"))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg not installed")
class RealTone(unittest.TestCase):
    def test_quiet_clip_lands_on_minus_14_and_video_is_copied(self):
        with tempfile.TemporaryDirectory() as d:
            final = Path(d) / "video.mp4"
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=108x192:r=30:d=3",
                            "-f", "lavfi", "-i", "sine=f=440:r=48000:d=3,volume=-30dB", "-shortest",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(final)], check=True)
            before = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(final)],
                                               capture_output=True, text=True).stdout)
            self.assertLess(lufs(final), -25)
            res = render.normalize_audio(str(final), silent=False)
            self.assertIsNotNone(res)
            self.assertAlmostEqual(lufs(final), -14, delta=1.0)
            after = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(final)],
                                              capture_output=True, text=True).stdout)
            v0 = next(s for s in before["streams"] if s["codec_type"] == "video")
            v1 = next(s for s in after["streams"] if s["codec_type"] == "video")
            a1 = next(s for s in after["streams"] if s["codec_type"] == "audio")
            self.assertEqual((v0["codec_name"], v0["profile"], v0["nb_frames"]), (v1["codec_name"], v1["profile"], v1["nb_frames"]))
            self.assertEqual((a1["codec_name"], a1["sample_rate"]), ("aac", "48000"))


if __name__ == "__main__":
    unittest.main()
