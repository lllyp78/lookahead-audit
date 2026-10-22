"""membership / weight 重放的合成数据测试。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import membership_time_replay as replay


class MembershipReplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.full = self.dir / "full.csv"
        self.cutoff = self.dir / "cutoff.csv"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path: Path, rows):
        pd.DataFrame(rows).to_csv(path, index=False)

    def compare_membership(self, full_rows, cutoff_rows, pnl=None):
        self.write(self.full, full_rows)
        self.write(self.cutoff, cutoff_rows)
        return replay.run(self.full, self.cutoff, ["entry_date"], ["symbol"],
                          "included", None, 0.0, 1e-9, pnl)

    def compare_weight(self, full_rows, cutoff_rows, threshold=0.0, tolerance=1e-9, pnl=None):
        self.write(self.full, full_rows)
        self.write(self.cutoff, cutoff_rows)
        return replay.run(self.full, self.cutoff, ["entry_date"], ["symbol"],
                          None, "weight", threshold, tolerance, pnl)

    def test_identical_membership(self):
        rows = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1},
                {"entry_date": "2020-01-01", "symbol": "b", "included": 0}]
        result = self.compare_membership(rows, rows)
        self.assertTrue(result["summary"]["identical"])

    def test_added_and_removed(self):
        full = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1},
                {"entry_date": "2020-01-01", "symbol": "b", "included": 1}]
        cutoff = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1},
                  {"entry_date": "2020-01-01", "symbol": "c", "included": 1}]
        result = self.compare_membership(full, cutoff)
        summary = result["summary"]
        self.assertEqual(summary["added_total"], 1)
        self.assertEqual(summary["removed_total"], 1)
        self.assertFalse(summary["identical"])

    def test_zero_and_absent_are_same(self):
        full = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1},
                {"entry_date": "2020-01-01", "symbol": "b", "included": 0}]
        cutoff = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1}]
        result = self.compare_membership(full, cutoff)
        self.assertTrue(result["summary"]["identical"])

    def test_weight_change(self):
        full = [{"entry_date": "2020-01-01", "symbol": "a", "weight": 0.5},
                {"entry_date": "2020-01-01", "symbol": "b", "weight": 0.3}]
        cutoff = [{"entry_date": "2020-01-01", "symbol": "a", "weight": 0.25},
                  {"entry_date": "2020-01-01", "symbol": "b", "weight": 0.3}]
        result = self.compare_weight(full, cutoff)
        summary = result["summary"]
        self.assertEqual(summary["weight_changed_total"], 1)
        self.assertEqual(summary["added_total"], 0)
        self.assertEqual(summary["removed_total"], 0)
        self.assertEqual(result["groups"][0]["weight_changed_examples"][0]["full"], 0.5)

    def test_weight_inclusion_change_is_not_weight_change(self):
        full = [{"entry_date": "2020-01-01", "symbol": "a", "weight": 0.0},
                {"entry_date": "2020-01-01", "symbol": "b", "weight": 0.5}]
        cutoff = [{"entry_date": "2020-01-01", "symbol": "a", "weight": 0.5},
                  {"entry_date": "2020-01-01", "symbol": "b", "weight": 0.0}]
        result = self.compare_weight(full, cutoff)
        summary = result["summary"]
        self.assertEqual(summary["added_total"], 1)
        self.assertEqual(summary["removed_total"], 1)
        self.assertEqual(summary["weight_changed_total"], 0)

    def test_weight_threshold(self):
        rows = [{"entry_date": "2020-01-01", "symbol": "a", "weight": 0.2}]
        result = self.compare_weight(rows, rows, threshold=0.1)
        self.assertTrue(result["summary"]["identical"])
        result = self.compare_weight(rows, rows, threshold=0.3)
        self.assertTrue(result["summary"]["identical"])

    def test_group_only_in_one_file(self):
        full = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1}]
        cutoff = [{"entry_date": "2020-01-02", "symbol": "a", "included": 1}]
        result = self.compare_membership(full, cutoff)
        self.assertEqual(result["summary"]["groups"], 2)
        self.assertEqual(result["summary"]["added_total"], 1)
        self.assertEqual(result["summary"]["removed_total"], 1)

    def test_pnl_sums(self):
        full = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1, "pnl": 100},
                {"entry_date": "2020-01-01", "symbol": "b", "included": 1, "pnl": -50},
                {"entry_date": "2020-01-01", "symbol": "c", "included": 1, "pnl": 30}]
        cutoff = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1, "pnl": 100},
                  {"entry_date": "2020-01-01", "symbol": "c", "included": 0, "pnl": 30},
                  {"entry_date": "2020-01-01", "symbol": "d", "included": 1, "pnl": 70}]
        result = self.compare_membership(full, cutoff, pnl="pnl")
        summary = result["summary"]
        self.assertEqual(summary["added_total"], 1)
        self.assertEqual(summary["removed_total"], 2)
        self.assertEqual(summary["added_pnl"], 70)
        self.assertEqual(summary["removed_pnl"], -20)

    def test_weight_tolerance(self):
        full = [{"entry_date": "2020-01-01", "symbol": "a", "weight": 0.5}]
        cutoff = [{"entry_date": "2020-01-01", "symbol": "a", "weight": 0.5000000001}]
        loose = self.compare_weight(full, cutoff, tolerance=1e-6)
        tight = self.compare_weight(full, cutoff, tolerance=1e-12)
        self.assertTrue(loose["summary"]["identical"])
        self.assertEqual(tight["summary"]["weight_changed_total"], 1)

    def test_duplicate_key_invalid(self):
        rows = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1},
                {"entry_date": "2020-01-01", "symbol": "a", "included": 0}]
        self.write(self.full, rows)
        self.write(self.cutoff, rows)
        with self.assertRaises(ValueError):
            replay.run(self.full, self.cutoff, ["entry_date"], ["symbol"],
                       "included", None, 0.0, 1e-9, None)

    def test_invalid_membership_is_two(self):
        rows = [{"entry_date": "2020-01-01", "symbol": "a", "included": 0.5}]
        self.write(self.full, rows)
        self.write(self.cutoff, rows)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(replay.main([
                "--full", str(self.full), "--cutoff", str(self.cutoff),
                "--group-columns", "entry_date", "--key-columns", "symbol",
                "--membership-column", "included"]), 2)

    def test_json_output_and_exit_codes(self):
        rows = [{"entry_date": "2020-01-01", "symbol": "a", "included": 1}]
        self.write(self.full, rows)
        self.write(self.cutoff, rows)
        out = self.dir / "replay.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = replay.main([
                "--full", str(self.full), "--cutoff", str(self.cutoff),
                "--group-columns", "entry_date", "--key-columns", "symbol",
                "--membership-column", "included", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.read_text())["summary"]["identical"])


if __name__ == "__main__":
    unittest.main()