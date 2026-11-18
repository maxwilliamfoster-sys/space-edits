"""Narrator (spacepipe/narrate.py): numbers read naturally, scripts only say what the plan shows, cues never overlap or overrun,
music ducks under the voice, the A/B arm is recorded as rendered, and a missing voice never breaks a render."""
import re
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analytics  # noqa: E402
import gate  # noqa: E402
from spacepipe import compose, facts, narrate, render  # noqa: E402
from spacepipe.lib import FFMPEG, load_assets  # noqa: E402

ALL = ["flash", "target", "timemachine", "guess", "countdown", "drift", *compose.TEMPLATES]
NO_DIGITS = re.compile(r"[\d%+#]")


class Numbers(unittest.TestCase):
    def test_british_integers(self):
        cases = {0: "zero", 13: "thirteen", 42: "forty-two", 250: "two hundred and fifty", 650: "six hundred and fifty",
                 1050: "one thousand and fifty", 6500: "six thousand five hundred", 7109: "seven thousand one hundred and nine",
                 26000: "twenty-six thousand", 100000: "one hundred thousand"}
        self.assertEqual(narrate.distance_words("100 million"), "a hundred million")
        self.assertEqual(narrate.distance_words("1,300"), "a thousand three hundred")
        for n, want in cases.items():
            self.assertEqual(narrate.int_words(n), want, n)

    def test_catalogue_distances(self):
        cases = {"6,500": "six thousand five hundred", "7500": "seven thousand five hundred", "2.8 billion": "two point eight billion",
                 "30 million": "thirty million", "12.5 million": "twelve point five million", "4.5 billion": "four point five billion"}
        for d, want in cases.items():
            self.assertEqual(narrate.distance_words(d), want, d)

    def test_scraped_fragments_are_not_spoken(self):
        for d in ("000", "", None, "unknown"):
            self.assertIsNone(narrate.distance_words(d), d)

    def test_every_catalogue_distance_reads_without_digits(self):
        for a in load_assets():
            w = narrate.distance_words(a.get("dist_ly"))
            if w:
                self.assertFalse(NO_DIGITS.search(w), (a["id"], w))

    def test_catalogue_names(self):
        cases = {"NGC 3507": "N G C thirty-five oh seven", "NGC 1300": "N G C thirteen hundred", "NGC 685": "N G C six eighty-five",
                 "M77": "Messier seventy-seven", "Messier 106": "Messier one oh six", "MACS0416": "Macs oh four one six",
                 "THE PILLARS OF CREATION": "the Pillars of Creation", "Arp 142": "Arp one forty-two"}
        for name, want in cases.items():
            self.assertEqual(narrate.spoken_name(name), want, name)

    def test_every_target_name_reads_without_digits(self):
        for a in compose.dist_pool(load_assets(), set()):
            name = compose.target_name(a)
            if name:
                self.assertFalse(NO_DIGITS.search(narrate.spoken_name(name)), name)


class Lines(unittest.TestCase):
    def test_every_fixed_zoom_line_has_a_spoken_version(self):
        pairs = facts.EARTH + facts.SUN + list(facts.PLANET.values()) + facts.MW + [
            ("A WORLD", "FAR FROM HOME"), ("ONE GALAXY", "OF HUNDREDS OF BILLIONS"), ("REAL IMAGE", "FROM A SPACE TELESCOPE"),
            ("ONE TINY PATCH OF SKY", "THOUSANDS OF GALAXIES"), ("MOST OF THESE SMUDGES", "ARE WHOLE GALAXIES")]
        for head, sub in pairs:
            line = narrate.spoken_pair(head, sub)
            self.assertTrue(line, (head, sub))
            self.assertFalse(NO_DIGITS.search(line), line)

    def test_distance_lines_follow_the_screen(self):
        self.assertEqual(narrate.spoken_pair("THIS GALAXY", "30 MILLION LIGHT-YEARS AWAY"), "This galaxy is about thirty million light-years away.")
        self.assertEqual(narrate.spoken_pair("THIS LIGHT", "LEFT 6,500 YEARS AGO"), "This light set off about six thousand five hundred years ago.")
        self.assertIsNone(narrate.spoken_pair("SOMETHING", "NEW"))

    def test_eras_and_facts_line_up(self):
        self.assertEqual(len(narrate.ERA_SPOKEN), len(facts.ERAS))
        self.assertLessEqual(set(narrate.TARGET_FACTS), set(compose.TARGET_NAMES.values()))
        for f in narrate.TARGET_FACTS.values():
            self.assertFalse(re.search(r"\d", f), f)


class Script(unittest.TestCase):
    def test_scripts_for_every_format(self):
        for t in ALL:
            for seed in (11, 12, 13):
                p = compose.plan(seed, t)
                cues = narrate.script(p)
                if t == "drift":
                    self.assertEqual(cues, [])      # "no talking"
                    continue
                self.assertTrue(cues, t)
                t_end = p["T0"] + p.get("total_beats", 60) * p["P"]
                prev = -1
                for c in cues:
                    self.assertLess(c["t0"], c["t1"], (t, c))
                    self.assertLessEqual(c["t1"], t_end, (t, c))
                    self.assertGreaterEqual(c["t0"], prev, (t, c))
                    prev = c["t0"]
                    for o in c["options"]:
                        self.assertFalse(NO_DIGITS.search(o), (t, o))

    def test_target_says_name_distance_and_era_from_the_plan(self):
        p = compose.plan(5, "target")
        name = p["texts"][0]["text"]
        a, b = narrate.script(p)
        self.assertIn(narrate.spoken_name(name), a["options"][0])
        d = narrate.distance_words(p["shots"][0]["dist_ly"])
        self.assertIn(f"about {d} light-years away", b["options"][0])
        self.assertEqual(b["options"][-1], f"It's about {d} light-years away.")   # the era is the part dropped first

    def test_unnamed_targets_get_a_line_true_of_any_such_place(self):
        self.assertEqual(narrate.place_fact({"tier": "nebula", "dist_ly": "7500"}), "It's inside our own galaxy, the Milky Way.")
        self.assertEqual(narrate.place_fact({"tier": "galaxy", "dist_ly": "30 million"}), "A whole galaxy, far beyond our own.")
        self.assertEqual(narrate.place_fact({"tier": "nebula", "dist_ly": "6.3 billion"}), "It lies far beyond our own galaxy.")
        self.assertIsNone(narrate.place_fact({"tier": "nebula", "dist_ly": "160,000"}))   # between: say nothing

    def test_time_machine_never_repeats_a_sentence(self):
        p = compose.plan(14, "timemachine")
        firsts = [c["options"][-1].split(" about ")[0] for c in narrate.script(p)]
        self.assertEqual(len(firsts), len(set(firsts)))

    def test_plural_targets(self):
        p = compose.plan(5, "target")
        p["texts"][0]["text"] = "THE PILLARS OF CREATION"
        self.assertTrue(narrate.script(p)[0]["options"][-1].startswith("These are the Pillars of Creation"))

    def test_draw_is_a_share_and_never_moves_a_plan(self):
        with mock.patch.object(compose, "NARRATION_SHARE", 0.5):
            arms = [compose.plan(s)["narrate"] for s in range(200, 260)]
        drawn = [x for x in arms if x is not None]
        self.assertTrue(0.25 < sum(drawn) / len(drawn) < 0.75)
        for s in (300, 301, 302):
            with mock.patch.object(compose, "NARRATION_SHARE", 0.0):
                off = compose.plan(s)
            with mock.patch.object(compose, "NARRATION_SHARE", 1.0):
                on = compose.plan(s)
            self.assertEqual({k: v for k, v in off.items() if k != "narrate"}, {k: v for k, v in on.items() if k != "narrate"})
            if on["template"] != "drift":
                self.assertEqual((off["narrate"], on["narrate"]), (False, True))
            else:
                self.assertIsNone(on["narrate"])


def fake_synth(text, speed):
    """2.5 words a second at the default speed."""
    return np.ones(int(len(text.split()) / 2.5 * narrate.SPEED / speed * narrate.SR), np.float32) * 0.1


class Fit(unittest.TestCase):
    def test_drops_optional_sentences_then_skips(self):
        cues = [narrate._cue(0.3, 2.0, "Short one here.", "And a long optional sentence that cannot fit at all."),
                narrate._cue(2.5, 3.0, "This one is far too long for half a second.")]
        out = narrate.fit(cues, synth=fake_synth)
        self.assertEqual([t for _, _, t, _ in out], ["Short one here."])

    def test_never_overlaps_and_keeps_a_gap(self):
        cues = [narrate._cue(0.3, 5.0, "One two three four five six seven eight."), narrate._cue(1.0, 9.0, "Nine ten eleven.")]
        out = narrate.fit(cues, synth=fake_synth)
        self.assertEqual(len(out), 2)
        self.assertGreaterEqual(out[1][0], out[0][1] + narrate.GAP - 1e-6)
        for t0, t1, _, x in out:
            self.assertAlmostEqual(t1 - t0, len(x) / narrate.SR, places=2)

    def test_track_places_clips(self):
        clip = np.full(narrate.SR // 2, 0.5, np.float32)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "n.wav"
            narrate.write_track([(1.0, 1.5, "x", clip)], 3.0, path)
            with wave.open(str(path)) as w:
                self.assertEqual(w.getframerate(), narrate.SR)
                y = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767
            self.assertAlmostEqual(len(y) / narrate.SR, 3.0, places=2)
            self.assertEqual(float(np.abs(y[:narrate.SR - 10]).max()), 0.0)
            self.assertAlmostEqual(float(y[int(1.2 * narrate.SR)]), 0.5, places=3)


class Duck(unittest.TestCase):
    @staticmethod
    def at(expr, t):
        # safe: expr is built by narrate.duck_expr from numbers in this test (ffmpeg expression syntax happens to be Python)
        return eval(expr, {"clip": lambda x, a, b: max(a, min(b, x)), "min": min, "t": t})

    def test_music_dips_under_the_voice_only(self):
        e = narrate.duck_expr([(2.0, 4.0), (6.0, 7.0)])
        g = 10 ** (narrate.DUCK_DB / 20)
        self.assertAlmostEqual(self.at(e, 0.5), 1.0)
        self.assertAlmostEqual(self.at(e, 3.0), g, places=3)
        self.assertAlmostEqual(self.at(e, 5.0), 1.0)
        self.assertAlmostEqual(self.at(e, 6.5), g, places=3)
        self.assertTrue(g < self.at(e, 1.85) < 1.0)       # ramps, no hard step
        self.assertEqual(narrate.duck_expr([]), "1")

    def test_mix_graph(self):
        p = dict(music=dict(start=3.0), narration=dict(cues=[dict(t0=0.3, t1=2.0, text="x")]))
        g = render.narration_mix(p, 1, 2, 10.0)
        for part in ("[1:a]atrim=start=3.00", "eval=frame", "[2:a]aresample=48000,deesser", "amix=inputs=2:duration=first:normalize=0", "[a]"):
            self.assertIn(part, g)
        self.assertNotIn("atrim", render.narration_mix({**p, "music_on": False}, 1, 2, 10.0))


class Arms(unittest.TestCase):
    def test_gate_records_the_arm_as_rendered(self):
        self.assertIsNone(gate.narrated({"narrate": None}))
        self.assertIsNone(gate.narrated({}))
        self.assertFalse(gate.narrated({"narrate": False}))
        self.assertFalse(gate.narrated({"narrate": True, "narration": {"cues": [], "error": "x"}}))   # voice failed
        self.assertTrue(gate.narrated({"narrate": True, "narration": {"cues": [{"t0": 0, "t1": 1, "text": "x"}]}}))
        plan = compose.plan(7, "guess")
        plan["narration"] = {"cues": [{"t0": 0, "t1": 1, "text": "x"}]}
        self.assertEqual(gate.entry_for(plan, [], "e1")["narrated"], gate.narrated(plan))

    def test_analytics_rows_carry_the_arm(self):
        sent = [dict(id="p1", sentAt="2026-11-10T10:00:00Z", metrics={"views": 900})]
        rows = analytics.rows(sent, {"e1": {"post": "p1"}}, [{"id": "e1", "template": "flash", "narrated": True}])
        self.assertIs(rows[0]["narrated"], True)

    def test_missing_voice_renders_without_narration(self):
        p = compose.plan(9, "flash")
        p["narrate"] = True
        with tempfile.TemporaryDirectory() as d, mock.patch.object(narrate, "MODEL_DIR", Path(d) / "none"):
            info = narrate.build(p, d)
        self.assertEqual(info["cues"], [])
        self.assertIn("FileNotFoundError", info["error"])
        self.assertFalse(gate.narrated(p))


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class RealMix(unittest.TestCase):
    def test_music_is_about_12_db_lower_under_the_voice(self):
        """A steady tone as the 'music', a silent voice track with one 'cue': the mixed tone must drop by DUCK_DB there."""
        with tempfile.TemporaryDirectory() as d:
            music, voice, out = Path(d) / "m.wav", Path(d) / "v.wav", Path(d) / "o.wav"
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:r=48000:d=12", str(music)], check=True)
            narrate.write_track([], 10.0, voice)   # 10 s: the music's own end fade starts at 8.2 s, after the windows measured
            p = dict(music=dict(start=0.0), narration=dict(cues=[dict(t0=3.0, t1=5.0, text="x")]))
            g = render.narration_mix(p, 0, 1, 10.0)
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(music), "-i", str(voice), "-filter_complex", g,
                            "-map", "[a]", "-t", "10", "-ac", "1", str(out)], check=True)
            with wave.open(str(out)) as w:
                sr = w.getframerate()
                y = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
            rms = lambda a, b: np.sqrt(np.mean(y[int(a * sr):int(b * sr)] ** 2))
            drop = 20 * np.log10(rms(3.6, 4.4) / rms(0.8, 1.6))
            self.assertAlmostEqual(drop, narrate.DUCK_DB, delta=1.0)
            self.assertAlmostEqual(20 * np.log10(rms(6.0, 6.8) / rms(0.8, 1.6)), 0.0, delta=0.5)   # back up after


@unittest.skipUnless(narrate.available(), "Kokoro model files not here (the workflows download them)")
class RealVoice(unittest.TestCase):
    def test_bm_george_speaks_at_a_calm_pace(self):
        text = "This light left about six thousand five hundred years ago."
        x = narrate.say(text)
        secs = len(x) / narrate.SR
        self.assertTrue(1.8 < len(text.split()) / secs < 3.4, secs)      # words a second
        self.assertLessEqual(float(np.abs(x).max()), 10 ** (narrate.PEAK_DB / 20) + 1e-3)


if __name__ == "__main__":
    unittest.main()
