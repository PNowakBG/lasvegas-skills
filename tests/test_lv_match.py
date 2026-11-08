"""Testy czystego dopasowania nazw drużyn i składni harnessu lv-place.

Uruchomienie:
    python3 -m unittest discover -s agent/lasvegas-skills-tap/tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "lv-executor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lv_match import (  # noqa: E402
    classify_search_failure,
    key_tokens,
    pick_best_tile,
    search_queries,
    search_trace_summary,
    team_coverage,
)


class KeyTokensTest(unittest.TestCase):
    def test_acf_prefix_is_generic(self):
        # Regresja 2026-11-08: „ACF” zostawało pierwszym tokenem i STS dostawał
        # zapytanie „acf” zamiast „fiorentina”.
        self.assertEqual(key_tokens("ACF Fiorentina"), ["fiorentina"])

    def test_cfc_suffix_is_generic(self):
        self.assertEqual(key_tokens("Genoa CFC"), ["genoa"])

    def test_common_prefixes_do_not_become_query(self):
        self.assertEqual(key_tokens("AC Milan"), ["milan"])
        self.assertEqual(key_tokens("FC Barcelona"), ["barcelona"])


class SearchQueriesTest(unittest.TestCase):
    def test_fiorentina_query_uses_club_not_prefix(self):
        queries = search_queries("ACF Fiorentina", "Juventus FC")
        self.assertEqual(queries[0], "fiorentina")
        self.assertIn("juventus", queries)
        self.assertNotIn("acf", queries)

    def test_rennais_has_rennes_alias(self):
        queries = search_queries("Stade Rennais FC 1901", "Lille OSC")
        self.assertIn("rennais", queries)
        self.assertIn("rennes", queries)

    def test_query_list_is_capped(self):
        self.assertLessEqual(len(search_queries("ACF Fiorentina", "Juventus FC")), 4)


class TeamCoverageTest(unittest.TestCase):
    def test_matches_both_teams(self):
        self.assertGreater(team_coverage("Fiorentina - Juventus", "ACF Fiorentina", "Juventus FC"), 0)

    def test_rejects_reversed_home_away(self):
        # Świadoma decyzja: dla 1x2 kolejność gospodarz/gość jest częścią zakładu.
        self.assertEqual(team_coverage("Juventus - Fiorentina", "ACF Fiorentina", "Juventus FC"), 0)

    def test_rejects_youth_team_tile(self):
        self.assertEqual(team_coverage("Fiorentina U19 - Juventus", "ACF Fiorentina", "Juventus FC"), 0)

    def test_accepts_city_alias(self):
        self.assertGreater(team_coverage("Genoa - Milan", "Genoa CFC", "AC Milan"), 0)


class TilePickTest(unittest.TestCase):
    def test_picks_the_real_match_and_skips_esport(self):
        tiles = [
            {"href": "/kursy/esport/fiorentina-juventus", "text": "Esports Fiorentina Juventus"},
            {"href": "/kursy/pilka-nozna/wlochy/serie-a/fiorentina-juventus", "text": "Fiorentina Juventus"},
        ]
        href, score = pick_best_tile(
            tiles,
            "ACF Fiorentina",
            "Juventus FC",
            lambda h, t: "/kursy/" in h and "esport" not in t,
        )
        self.assertEqual(href, "/kursy/pilka-nozna/wlochy/serie-a/fiorentina-juventus")
        self.assertGreaterEqual(score, 2)

    def test_returns_empty_when_no_tile_matches(self):
        href, score = pick_best_tile(
            [{"href": "/kursy/pilka-nozna/anglia/epl/arsenal-chelsea", "text": "Arsenal Chelsea"}],
            "ACF Fiorentina",
            "Juventus FC",
        )
        self.assertEqual((href, score), ("", 0))

    def test_trace_summary_is_short_and_readable(self):
        summary = search_trace_summary(
            [
                {"query": "fiorentina", "tiles": 0, "score": 0},
                {"query": "juventus", "tiles": 12, "score": 2},
            ]
        )
        self.assertIn("fiorentina: 0 kafli", summary)
        self.assertIn("juventus: 12 kafli", summary)


class SearchFailureClassifierTest(unittest.TestCase):
    def test_no_tiles_is_retryable_search_failure(self):
        self.assertEqual(
            classify_search_failure([{"query": "fiorentina", "tiles": 0, "score": 0}]),
            ("failed", "event_not_found"),
        )

    def test_tiles_without_match_is_not_offering(self):
        self.assertEqual(
            classify_search_failure([{"query": "fiorentina", "tiles": 12, "score": 0}]),
            ("skipped", "bookmaker_not_offering"),
        )

    def test_mixed_trace_is_not_offering(self):
        self.assertEqual(
            classify_search_failure(
                [
                    {"query": "fiorentina", "tiles": 0, "score": 0},
                    {"query": "juventus", "tiles": 12, "score": 0},
                ]
            ),
            ("skipped", "bookmaker_not_offering"),
        )


class HarnessSyntaxTest(unittest.TestCase):
    def test_harness_program_compiles(self):
        path = SCRIPTS / "lv-place.py"
        spec = importlib.util.spec_from_file_location("lv_place_script", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("from lv_match import", module.HARNESS_PROGRAM)
        self.assertIn("LV_SKILL_DIR", module.HARNESS_PROGRAM)
        compile(module.HARNESS_PROGRAM, "<lv-place-harness>", "exec")

    def test_harness_head_imports_the_real_module(self):
        path = SCRIPTS / "lv-place.py"
        spec = importlib.util.spec_from_file_location("lv_place_script_head", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        os.environ["LV_SKILL_DIR"] = str(SCRIPTS)
        head = module.HARNESS_PROGRAM.split("# ---------------- main ----------------")[0]
        namespace: dict = {}
        exec(compile(head, "<lv-place-harness-head>", "exec"), namespace)
        self.assertEqual(namespace["search_queries"]("ACF Fiorentina", "Juventus FC")[0], "fiorentina")
        self.assertEqual(
            namespace["classify_search_failure"]([{"query": "x", "tiles": 4, "score": 0}]),
            ("skipped", "bookmaker_not_offering"),
        )


if __name__ == "__main__":
    unittest.main()
