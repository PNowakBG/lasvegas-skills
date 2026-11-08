"""Test funkcji `report_or_warn` z lv-executor-cycle.sh.

Nie uruchamia całego cyklu — wyciąga samą funkcję i podstawia `log`/`lv_api_log`,
żeby sprawdzić, że nieudany raport zostawia ślad, a udany nie hałasuje.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

CYCLE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "lv-executor"
    / "scripts"
    / "lv-executor-cycle.sh"
)


def report_function() -> str:
    text = CYCLE.read_text()
    start = text.index("report_or_warn() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


class ReportOrWarnTest(unittest.TestCase):
    def run_case(self, api_exit: int) -> subprocess.CompletedProcess:
        script = f"""set -euo pipefail
LOG=$(mktemp)
log() {{ echo "$*" >> "$LOG"; }}
lv_api_log() {{ return {api_exit}; }}
{report_function()}
report_or_warn "skipped bet-1" skipped bet-1 odds_drift "kurs 1.64 → 1.60"
cat "$LOG"
rm -f "$LOG"
"""
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_failed_report_is_logged(self):
        result = self.run_case(1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OSTRZEŻENIE: raport skipped bet-1 nie przeszedł", result.stdout)

    def test_successful_report_is_silent(self):
        result = self.run_case(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("OSTRZEŻENIE", result.stdout)


if __name__ == "__main__":
    unittest.main()
