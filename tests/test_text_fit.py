"""No on-screen line may run off the 1080 px frame (autopilot review 2026-11-18: 'THIS LIGHT LEFT BEFORE THE GREAT
PYRAMID WAS BUILT' at Arial Bold 36 measured 1,064 px, so it touched both edges and sat under TikTok's buttons)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spacepipe import compose, facts, render

NO_HISTORY = {"usage": {}, "edits": []}


def plan(seed, template):
    with mock.patch.object(compose, "history", return_value=NO_HISTORY):
        return compose.plan(seed, template)


class TextFits(unittest.TestCase):
    def test_estimate_matches_the_measured_overflow(self):
        # measured with Pillow on arialbd.ttf: 1063.9 px
        w = render.text_width("THIS LIGHT LEFT BEFORE THE GREAT PYRAMID WAS BUILT", 36, "arial")
        self.assertGreater(w, 1000)
        self.assertLess(w, 1130)

    def test_every_layer_fits_in_every_format(self):
        for template in ("target", "flash", "timemachine", "guess", "countdown", "drift"):
            for seed in range(25):
                p = plan(f"fit{seed}", template)
                for L in render.text_layers(p):
                    self.assertLessEqual(render.text_width(L["text"], L["size"], L["font"]), render.MAX_TEXT_W,
                                         f"{template} {seed}: {L['text']!r} at {L['size']}")

    def test_every_era_line_fits_after_wrapping(self):
        p = plan("fit0", "target")
        for _, era in facts.ERAS:
            tx = [dict(text="THIS LIGHT LEFT " + era, k0=5, k1=10, size=36, y="y2", font="arial")]
            layers = render.text_layers(dict(p, texts=tx, hook=""))
            self.assertTrue(layers)
            for L in layers:
                self.assertLessEqual(render.text_width(L["text"], L["size"], L["font"]), render.MAX_TEXT_W)
            self.assertEqual(" ".join(L["text"] for L in layers), "THIS LIGHT LEFT " + era)

    def test_wrapped_lines_stack_downwards_and_share_timing(self):
        p = plan("fit0", "target")
        tx = [dict(text="THIS LIGHT LEFT BEFORE THE GREAT PYRAMID WAS BUILT", k0=5, k1=10, size=36, y=1130, font="arial")]
        a, b = render.text_layers(dict(p, texts=tx, hook=""))
        self.assertEqual(a["y"], 1130)
        self.assertGreater(b["y"], a["y"] + 36)
        self.assertEqual((a["t0"], a["alpha"]), (b["t0"], b["alpha"]))

    def test_short_lines_are_untouched(self):
        p = plan("fit0", "target")
        tx = [dict(text="REAL TELESCOPE FOOTAGE", k0=0, k1=5, size=40, y=1130, font="arial")]
        (L,) = render.text_layers(dict(p, texts=tx, hook=""))
        self.assertEqual((L["text"], L["y"]), ("REAL TELESCOPE FOOTAGE", 1130))


if __name__ == "__main__":
    unittest.main()
