"""决策时点重放的合成数据测试，不依赖私有策略数据。"""

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

import decision_time_replay as replay


def write(path: Path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


class DecisionTimeReplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.full = self.dir / "full.csv"
        self.cutoff = self.dir / "cutoff.csv"

    def tearDown(self):
        self.temp.cleanup()

    def run_compare(self, full_rows, cutoff_rows, top_k=2, ascending=False):
        write(self.full, full_rows)
        write(self.cutoff, cutoff_rows)
        return replay.run(self.full, self.cutoff, ["trade_date"],
                          ["symbol"], "score", top_k, ascending)

    def test_identical_is_zero(self):
        rows = [
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "3"},
            {"trade_date": "2020-01-01", "symbol": "000002", "score": "2"},
        ]
        result = self.run_compare(rows, rows)
        self.assertTrue(result["summary"]["identical"])
        self.assertEqual(result["summary"]["top1_changed"], 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(replay.main([
                "--full", str(self.full), "--cutoff", str(self.cutoff),
                "--group-columns", "trade_date", "--key-columns", "symbol",
                "--rank-column", "score"]), 0)

    def test_top1_change_is_one(self):
        full = [
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "3"},
            {"trade_date": "2020-01-01", "symbol": "000002", "score": "2"},
        ]
        cutoff = [
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "1"},
            {"trade_date": "2020-01-01", "symbol": "000002", "score": "2"},
        ]
        result = self.run_compare(full, cutoff)
        self.assertFalse(result["summary"]["identical"])
        self.assertEqual(result["summary"]["top1_changed"], 1)
        self.assertEqual(result["groups"][0]["jaccard"], 1.0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(replay.main([
                "--full", str(self.full), "--cutoff", str(self.cutoff),
                "--group-columns", "trade_date", "--key-columns", "symbol",
                "--rank-column", "score"]), 1)

    def test_missing_key_is_difference(self):
        full = [
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "3"},
            {"trade_date": "2020-01-01", "symbol": "000002", "score": "2"},
        ]
        cutoff = [
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "3"},
        ]
        result = self.run_compare(full, cutoff)
        self.assertEqual(result["summary"]["missing_total"], 1)
        self.assertEqual(result["groups"][0]["missing_examples"], ["000002"])
        self.assertFalse(result["summary"]["identical"])

    def test_group_only_in_one_file_is_difference(self):
        full = [{"trade_date": "2020-01-01", "symbol": "000001", "score": "3"}]
        cutoff = [{"trade_date": "2020-01-02", "symbol": "000001", "score": "3"}]
        result = self.run_compare(full, cutoff)
        self.assertEqual(result["summary"]["groups"], 2)
        self.assertFalse(result["summary"]["identical"])

    def test_tie_break_is_key_ascending(self):
        rows = [
            {"trade_date": "2020-01-01", "symbol": "000002", "score": "3"},
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "3"},
        ]
        result = self.run_compare(rows, rows, top_k=1)
        self.assertEqual(result["groups"][0]["full_top"], ["000001"])

    def test_ascending_key(self):
        full = [
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "3"},
            {"trade_date": "2020-01-01", "symbol": "000002", "score": "2"},
        ]
        result = self.run_compare(full, full, top_k=1, ascending=True)
        self.assertEqual(result["groups"][0]["full_top"], ["000002"])

    def test_spearman_on_common_keys(self):
        full = [
            {"trade_date": "2020-01-01", "symbol": "a", "score": "3"},
            {"trade_date": "2020-01-01", "symbol": "b", "score": "2"},
            {"trade_date": "2020-01-01", "symbol": "c", "score": "1"},
        ]
        cutoff = [
            {"trade_date": "2020-01-01", "symbol": "a", "score": "1"},
            {"trade_date": "2020-01-01", "symbol": "b", "score": "2"},
            {"trade_date": "2020-01-01", "symbol": "c", "score": "3"},
        ]
        result = self.run_compare(full, cutoff)
        self.assertAlmostEqual(result["groups"][0]["spearman"], -1.0)

    def test_duplicate_key_is_invalid(self):
        rows = [
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "3"},
            {"trade_date": "2020-01-01", "symbol": "000001", "score": "2"},
        ]
        write(self.full, rows)
        write(self.cutoff, rows)
        with self.assertRaises(ValueError):
            replay.run(self.full, self.cutoff, ["trade_date"],
                       ["trade_date", "symbol"], "score", 2, False)

    def test_invalid_rank_returns_two(self):
        write(self.full, [{"trade_date": "2020-01-01", "symbol": "a", "score": "x"}])
        write(self.cutoff, [{"trade_date": "2020-01-01", "symbol": "a", "score": "1"}])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(replay.main([
                "--full", str(self.full), "--cutoff", str(self.cutoff),
                "--group-columns", "trade_date", "--key-columns", "symbol",
                "--rank-column", "score"]), 2)

    def test_json_output(self):
        rows = [{"trade_date": "2020-01-01", "symbol": "a", "score": "1"}]
        write(self.full, rows)
        write(self.cutoff, rows)
        out = self.dir / "replay.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = replay.main([
                "--full", str(self.full), "--cutoff", str(self.cutoff),
                "--group-columns", "trade_date", "--key-columns", "symbol",
                "--rank-column", "score", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.read_text())["summary"]["identical"])


if __name__ == "__main__":
    unittest.main()