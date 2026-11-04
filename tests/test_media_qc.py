"""media_qc against real seeded clips made with whatever FFmpeg this machine resolves.

space-edits/tests/test_media_qc.py is a copy of this file, so these tests also run on the FFmpeg that
Space's CI installs (8.0.1 on ubuntu-latest, checked 2026-11-04). Update both together.

Set FFMPEG_BIN to run the whole file against another FFmpeg build:
    FFMPEG_BIN=C:/ffmpeg/ffmpeg-n7.1-latest-win64-gpl-shared-7.1/bin/ffmpeg.exe py -m unittest tests.test_media_qc
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import media_qc  # noqa: E402

W, H = 270, 480
CLEAN_AUDIO = "anoisesrc=c=pink:a=1:r=48000:seed=7:d={d},alimiter=limit=0.7:level=disabled"
VIDEO = "testsrc2=s=270x480:r=30:d={d}"


def _make(path, *, video=VIDEO, audio=CLEAN_AUDIO, vf=None, af=None, d=6, audio_d=None, ar=None, extra=()):
    ff = media_qc.ffmpeg_bin()
    args = [ff, "-y", "-v", "error", "-f", "lavfi", "-i", video.format(d=d)]
    if audio:
        args += ["-f", "lavfi", "-i", audio.format(d=audio_d if audio_d is not None else d)]
    if vf:
        args += ["-vf", vf]
    if af and audio:
        args += ["-af", af]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "192k"]
        if ar:
            args += ["-ar", str(ar)]
    subprocess.run(args + list(extra) + ["-movflags", "+faststart", path], check=True)
    return path


class Clips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="media_qc_")
        version = subprocess.run([media_qc.ffmpeg_bin(), "-hide_banner", "-version"], capture_output=True, text=True)
        print("media_qc tests on:", version.stdout.splitlines()[0])  # the proof of which FFmpeg these ran against
        p = lambda n: os.path.join(cls.tmp, n + ".mp4")
        cls.clean = _make(p("clean"))
        cls.black = _make(p("black"), vf="drawbox=color=black:t=fill:enable='between(t,2,3)'")
        cls.black_open = _make(p("black_open"), vf="drawbox=color=black:t=fill:enable='lt(t,0.3)'")
        cls.freeze = _make(p("freeze"), vf="loop=loop=120:size=1:start=45,setpts=N/30/TB,trim=duration=7", d=7)
        cls.silence = _make(p("silence"), af="volume=enable='between(t,1,4.5)':volume=0")
        cls.quiet = _make(p("quiet"), af="volume=-20dB")
        cls.loud_peak = _make(p("loud_peak"), audio="sine=f=997:r=48000:d={d}", af="afade=t=in:d=0.3,volume=17.4dB")    # peak about 0.93; the fade stops AAC overshooting past 1.0 at the very start
        cls.clipped = _make(p("clipped"), af="volume=24dB,aformat=sample_fmts=s16")
        cls.rate44 = _make(p("rate44"), ar=44100)
        cls.rate96 = _make(p("rate96"), af="loudnorm=I=-14:TP=-1.5:LRA=11")
        cls.short_audio = _make(p("short_audio"), audio_d=4.5)
        cls.flash4 = _make(p("flash4"), video="color=white:s=270x480:r=30:d={d}",
                           vf="drawbox=color=black:t=fill:enable='lt(mod(t,0.25),0.125)'")
        cls.flash2 = _make(p("flash2"), video="color=white:s=270x480:r=30:d={d}",
                           vf="drawbox=color=black:t=fill:enable='lt(mod(t,0.5),0.25)'")
        cls.no_audio = _make(p("no_audio"), audio=None)
        cls.digital_silence = _make(p("digital_silence"), audio="anullsrc=r=48000:cl=stereo:d={d}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rules(self, path, **kw):
        r = media_qc.check(path, **kw)
        self.assertEqual(r["failures"], [], r["failures"])     # a seeded fault is a warning, not a crash
        return {w["rule"] for w in r["flags"]}, r

    # --- clean clip
    def test_clean_clip_has_no_warnings(self):
        rules, r = self.rules(self.clean)
        self.assertEqual(rules, set(), r["warnings"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["metrics"]["sample_rate"], 48000)
        self.assertGreater(r["metrics"]["seconds_taken"], 0)

    # --- each seeded fault trips exactly its rule
    def test_black_stretch(self):
        rules, r = self.rules(self.black)
        self.assertIn("black", rules)
        self.assertAlmostEqual(r["metrics"]["longest_black"], 1.0, delta=0.1)

    def test_black_in_first_second(self):
        rules, r = self.rules(self.black_open)
        self.assertIn("black_open", rules)
        self.assertNotIn("black", rules)                        # 0.3 s is under the stretch limit

    def test_freeze(self):
        rules, r = self.rules(self.freeze)
        self.assertIn("freeze", rules)
        self.assertGreater(r["metrics"]["longest_freeze"], 3.0)

    def test_freeze_allowance_per_format(self):
        rules, _ = self.rules(self.freeze, max_freeze=10.0)
        self.assertNotIn("freeze", rules)

    def test_silence(self):
        rules, r = self.rules(self.silence)
        self.assertIn("silence", rules)
        self.assertAlmostEqual(r["metrics"]["longest_silence"], 3.5, delta=0.3)

    def test_quiet_mix_is_out_of_the_loudness_window(self):
        rules, r = self.rules(self.quiet)
        self.assertIn("loudness", rules)
        self.assertLess(r["metrics"]["lufs"], -25)

    def test_true_peak_above_minus_one(self):
        rules, r = self.rules(self.loud_peak)
        self.assertIn("true_peak", rules)
        self.assertGreater(r["metrics"]["true_peak"], -1.0)

    def test_clipping(self):
        rules, r = self.rules(self.clipped)
        self.assertIn("clipping", rules)
        self.assertGreater(r["metrics"]["clipped_runs"], 0)

    def test_hot_but_unclipped_tone_is_not_clipping(self):
        rules, _ = self.rules(self.loud_peak, lufs=(-60, 0))
        self.assertNotIn("clipping", rules)

    def test_sample_rate_44k(self):
        rules, r = self.rules(self.rate44)
        self.assertIn("sample_rate", rules)
        self.assertEqual(r["metrics"]["sample_rate"], 44100)

    def test_loudnorm_without_resample_is_flagged_as_not_48k(self):
        rules, r = self.rules(self.rate96)
        self.assertIn("sample_rate", rules)
        self.assertEqual(r["metrics"]["sample_rate"], 96000)

    def test_audio_shorter_than_video(self):
        rules, r = self.rules(self.short_audio)
        self.assertIn("audio_short", rules)
        self.assertAlmostEqual(r["metrics"]["audio_short_by"], 1.5, delta=0.1)

    def test_flash_4hz_is_flagged_as_an_estimate(self):
        rules, r = self.rules(self.flash4)
        self.assertIn("flash_estimate", rules)
        self.assertAlmostEqual(r["metrics"]["flash_est_per_s"], 4.0, delta=0.5)
        msg = next(w["message"] for w in r["flags"] if w["rule"] == "flash_estimate")
        self.assertIn("estimate", msg)
        self.assertIn("not a compliance", msg)

    def test_flash_2hz_is_below_the_limit(self):
        rules, r = self.rules(self.flash2)
        self.assertNotIn("flash_estimate", rules)
        self.assertAlmostEqual(r["metrics"]["flash_est_per_s"], 2.0, delta=0.5)

    # --- audio absent
    def test_no_audio_stream_is_flagged_when_audio_is_expected(self):
        rules, _ = self.rules(self.no_audio)
        self.assertIn("no_audio", rules)

    def test_no_audio_is_fine_for_a_silent_format(self):
        rules, _ = self.rules(self.no_audio, audio=False)
        self.assertEqual(rules, set())

    def test_digital_silence_parses_minus_inf_peak(self):
        rules, r = self.rules(self.digital_silence)
        self.assertIn("loudness", rules)
        self.assertIn("silence", rules)
        self.assertIsNone(r["metrics"]["true_peak"])

    # --- warn-only until promoted
    def test_rules_never_block_unless_promoted(self):
        r = media_qc.check(self.rate44)
        self.assertTrue(r["ok"])
        self.assertEqual(r["failures"], [])
        self.assertEqual(media_qc.ENFORCED, frozenset())        # nothing is promoted yet
        r = media_qc.check(self.rate44, enforce={"sample_rate"})
        self.assertFalse(r["ok"])
        self.assertTrue(any("sample_rate" in f for f in r["failures"]))

    def test_messages_hold_no_paths(self):
        r = media_qc.check(self.black)
        for line in r["warnings"]:
            self.assertTrue(line.startswith("QC "), line)
            self.assertNotIn(self.tmp, line)

    def test_log_line_is_one_greppable_line(self):
        line = media_qc.log_line(media_qc.check(self.rate44), "clip.mp4")
        self.assertTrue(line.startswith("[qc] "), line)
        self.assertNotIn("\n", line)
        self.assertIn("sample_rate", line)

    # --- a check that cannot run is a failure
    def test_missing_file_fails(self):
        r = media_qc.check(os.path.join(self.tmp, "nope.mp4"))
        self.assertFalse(r["ok"])
        self.assertTrue(any("could not run" in f for f in r["failures"]))

    def test_garbage_file_fails(self):
        p = os.path.join(self.tmp, "garbage.mp4")
        with open(p, "wb") as fh:
            fh.write(os.urandom(4096))
        r = media_qc.check(p)
        self.assertFalse(r["ok"])
        self.assertTrue(any("could not run" in f for f in r["failures"]))

    def test_truncated_file_fails(self):
        p = os.path.join(self.tmp, "cut.mp4")
        with open(self.clean, "rb") as src, open(p, "wb") as dst:
            data = src.read()
            dst.write(data[:len(data) // 2])
        r = media_qc.check(p)
        self.assertFalse(r["ok"], r)

    def test_missing_ffmpeg_fails(self):
        r = media_qc.check(self.clean, ffmpeg=os.path.join(self.tmp, "no_ffmpeg.exe"))
        self.assertFalse(r["ok"])
        self.assertTrue(any("could not run" in f for f in r["failures"]))

    def test_unknown_bot_fails(self):
        r = media_qc.check(self.clean, bot="not-a-bot")
        self.assertFalse(r["ok"])
        self.assertTrue(any("could not run" in f for f in r["failures"]))

    def test_log_without_a_loudness_summary_fails(self):
        with self.assertRaises(media_qc.QCError):
            media_qc.parse_log("Stream mapping:\n  nothing useful\n", has_audio=True, duration=5.0)

    def test_log_without_a_peak_line_fails(self):
        log = "[Parsed_ebur128_1 @ 0x1] Summary:\n  Integrated loudness:\n    I:         -14.0 LUFS\n"
        with self.assertRaises(media_qc.QCError):
            media_qc.parse_log(log, has_audio=True, duration=5.0)


class ParseLog(unittest.TestCase):
    """Fixed text, in the log format of each FFmpeg version the bots use."""
    LOG = """
[blackdetect @ 0000] black_start:2 black_end:3.1 black_duration:1.1
[blackdetect @ 0000] black_start:5 black_end:5.2 black_duration:0.2
[freezedetect @ 0000] lavfi.freezedetect.freeze_start: 1.5
[freezedetect @ 0000] lavfi.freezedetect.freeze_duration: 2.25
[freezedetect @ 0000] lavfi.freezedetect.freeze_end: 3.75
[freezedetect @ 0000] lavfi.freezedetect.freeze_start: 8
[silencedetect @ 0000] silence_start: -0.0213
[silencedetect @ 0000] silence_end: 1.5 | silence_duration: 1.5213
[silencedetect @ 0000] silence_start: 9
[Parsed_ebur128_1 @ 0000] Summary:

  Integrated loudness:
    I:         -14.3 LUFS
    Threshold: -24.5 LUFS

  Loudness range:
    LRA:         3.2 LU

  True peak:
    Peak:       -1.4 dBFS
"""

    def test_parses_every_measure(self):
        m = media_qc.parse_log(self.LOG, has_audio=True, duration=10.0)
        self.assertEqual(m["longest_black"], 1.1)
        self.assertEqual(m["black_starts"], [2.0, 5.0])
        self.assertEqual(m["longest_freeze"], 2.25)                  # 8 -> 10 is only 2 s
        self.assertEqual(m["lufs"], -14.3)
        self.assertEqual(m["true_peak"], -1.4)
        self.assertEqual(m["longest_silence"], 1.52)

    def test_open_freeze_runs_to_the_end_of_the_video(self):
        m = media_qc.parse_log(self.LOG, has_audio=True, duration=20.0)
        self.assertEqual(m["longest_freeze"], 12.0)
        self.assertEqual(m["longest_silence"], 11.0)

    def test_no_audio_needs_no_loudness_lines(self):
        m = media_qc.parse_log("[blackdetect @ 0] black_start:1 black_end:2 black_duration:1", has_audio=False, duration=5)
        self.assertEqual(m["longest_black"], 1.0)
        self.assertNotIn("lufs", m)


class Flash(unittest.TestCase):
    def wave(self, hz, seconds=4, fps=30, cells=100, low=0.02, high=0.9):
        n = int(seconds * fps)
        on = (np.arange(n) * 2 * hz // fps) % 2 == 0
        return np.where(on[:, None], high, low) * np.ones((n, cells), np.float32)

    def test_counts_pairs_per_second(self):
        for hz in (1, 2, 4, 5):
            self.assertAlmostEqual(media_qc.flash_estimate(self.wave(hz), 30, 0.25), hz, delta=0.5, msg=str(hz))

    def test_small_swings_are_not_flashes(self):
        self.assertEqual(media_qc.flash_estimate(self.wave(4, low=0.40, high=0.45), 30, 0.25), 0.0)

    def test_swings_entirely_above_the_darker_limit_are_not_flashes(self):
        self.assertEqual(media_qc.flash_estimate(self.wave(4, low=0.82, high=1.0), 30, 0.25), 0.0)

    def test_small_area_only_counts_at_the_stricter_limit(self):
        lum = self.wave(4)
        lum[:, 15:] = 0.5                                     # only 15% of the cells flash
        self.assertEqual(media_qc.flash_estimate(lum, 30, 0.25), 0.0)
        self.assertAlmostEqual(media_qc.flash_estimate(lum, 30, 0.10), 4.0, delta=0.5)

    def test_one_slow_fade_is_not_a_flash(self):
        lum = np.linspace(0.0, 1.0, 120, dtype=np.float32)[:, None] * np.ones((120, 10), np.float32)
        self.assertEqual(media_qc.flash_estimate(lum, 30, 0.25), 0.0)


class Clipping(unittest.TestCase):
    def test_flat_tops_are_counted(self):
        tone = np.clip(4 * np.sin(np.arange(48000) * 2 * np.pi * 100 / 48000), -1, 1).astype(np.float32)
        self.assertGreater(media_qc.clipped_runs(tone.reshape(-1, 1)), 100)

    def test_a_clean_tone_has_none(self):
        tone = (0.7 * np.sin(np.arange(48000) * 2 * np.pi * 100 / 48000)).astype(np.float32)
        self.assertEqual(media_qc.clipped_runs(tone.reshape(-1, 1)), 0)

    def test_one_channel_clipping_counts(self):
        tone = np.clip(4 * np.sin(np.arange(4800) * 2 * np.pi * 100 / 48000), -1, 1).astype(np.float32)
        quiet = tone * 0.1
        self.assertGreater(media_qc.clipped_runs(np.stack([quiet, tone], axis=1)), 0)


if __name__ == "__main__":
    unittest.main()
