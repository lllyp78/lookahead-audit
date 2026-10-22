"""执行层窗口量进入排序的合成例子测试。"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "examples"))

import decision_time_replay as replay
import demo_execution_rank_leak as demo


class ExecutionRankExampleTest(unittest.TestCase):
    def compare(self, out: Path, mode: str, top_k: int = 2):
        full, cutoff = demo.write_case(out, mode)
        return replay.run(full, cutoff, ["entry_date"], ["symbol"],
                          "score", top_k, False)

    def test_clean_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.compare(Path(tmp) / "clean", "clean")
            summary = result["summary"]
            self.assertTrue(summary["identical"])
            self.assertEqual(summary["top1_changed"], 0)
            self.assertEqual(summary["topk_changed"], 0)
            self.assertEqual(summary["missing_total"], 0)
            self.assertEqual(summary["extra_total"], 0)

    def test_leaky_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.compare(Path(tmp) / "leaky", "leaky")
            summary = result["summary"]
            self.assertFalse(summary["identical"])
            self.assertEqual(summary["groups"], 2)
            self.assertEqual(summary["top1_changed"], 2)
            self.assertEqual(summary["topk_changed"], 2)
            self.assertEqual(summary["missing_total"], 0)
            self.assertEqual(summary["extra_total"], 0)
            self.assertAlmostEqual(summary["mean_jaccard"], 1 / 3)

    def test_leaky_top1_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.compare(Path(tmp) / "leaky1", "leaky", top_k=1)
            summary = result["summary"]
            self.assertFalse(summary["identical"])
            self.assertEqual(summary["top1_changed"], 2)
            self.assertEqual(summary["topk_changed"], 2)

    def test_cutoff_is_auction_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, cutoff = demo.write_case(Path(tmp) / "case", "leaky")
            frame = pd.read_csv(cutoff)
            self.assertTrue((frame["score"] == frame["auction_amount"]).all())

    def test_clean_full_equals_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            full, cutoff = demo.write_case(Path(tmp) / "case", "clean")
            pd.testing.assert_frame_equal(pd.read_csv(full), pd.read_csv(cutoff))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                clean = demo.main(["--out", str(Path(tmp) / "cli-clean"), "--mode", "clean"])
                leaky = demo.main(["--out", str(Path(tmp) / "cli-leaky"), "--mode", "leaky"])
            self.assertEqual(clean, 0)
            self.assertEqual(leaky, 1)

    def test_invalid_top_k_is_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            full, cutoff = demo.write_case(Path(tmp) / "case", "clean")
            with self.assertRaises(ValueError):
                replay.run(full, cutoff, ["entry_date"], ["symbol"], "score", 0, False)


if __name__ == "__main__":
    unittest.main()