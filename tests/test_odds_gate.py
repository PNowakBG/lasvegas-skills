"""Bramka kursu w lv-place: próg opłacalności ze zlecenia zamiast sztywnych −2 %.

Uruchomienie:
    python3 -m unittest discover -s agent/lasvegas-skills-tap/tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "lv-executor" / "scripts"


def harness_odds_ok(order: dict):
    """`odds_ok` z programu harnessu, wykonanego z danym zleceniem w środowisku."""
    spec = importlib.util.spec_from_file_location("lv_place_script_odds", SCRIPTS / "lv-place.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    os.environ["LV_SKILL_DIR"] = str(SCRIPTS)
    os.environ["LV_PLACE_ORDER"] = json.dumps(order)
    try:
        head = module.HARNESS_PROGRAM.split("# ---------------- main ----------------")[0]
        namespace: dict = {}
        exec(compile(head, "<lv-place-harness-head>", "exec"), namespace)
    finally:
        os.environ.pop("LV_PLACE_ORDER", None)
    return namespace["odds_ok"]


class OddsGateTest(unittest.TestCase):
    def test_without_floor_keeps_two_percent_tolerance(self):
        ok = harness_odds_ok({"odds": 2.0})
        self.assertTrue(ok(2.3, 2.0))
        self.assertTrue(ok(1.97, 2.0))
        self.assertFalse(ok(1.95, 2.0))

    def test_floor_accepts_profitable_drop_beyond_two_percent(self):
        # Prod 02.10: 2.20 → 2.10 (−4,5 %) odrzucone, choć próg opłacalności był niżej.
        ok = harness_odds_ok({"odds": 2.2, "oddsFloor": 2.05})
        self.assertTrue(ok(2.1, 2.2))

    def test_floor_rejects_unprofitable_price_inside_two_percent(self):
        ok = harness_odds_ok({"odds": 2.2, "oddsFloor": 2.18})
        self.assertFalse(ok(2.17, 2.2))

    def test_big_drop_is_a_mismatch_even_above_floor(self):
        # Prod 12.09: 3.60 → 2.25 to była inna linia, nie ruch ceny.
        ok = harness_odds_ok({"odds": 3.6, "oddsFloor": 2.0})
        self.assertFalse(ok(2.25, 3.6))

    def test_rule_minimum_always_applies(self):
        ok = harness_odds_ok({"odds": 1.31, "minOdds": 1.3})
        self.assertFalse(ok(1.29, 1.31))
        self.assertTrue(ok(1.3, 1.31))

    def test_garbage_floor_falls_back_to_tolerance(self):
        ok = harness_odds_ok({"odds": 2.0, "oddsFloor": "brak", "minOdds": None})
        self.assertTrue(ok(1.97, 2.0))
        self.assertFalse(ok(1.9, 2.0))


if __name__ == "__main__":
    unittest.main()
