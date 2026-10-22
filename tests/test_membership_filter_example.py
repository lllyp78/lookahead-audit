"""候选 membership 使用 T+1 开盘价的合成例子测试。"""

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

import membership_time_replay as replay
import demo_membership_filter_leak as demo


class MembershipFilterExampleTest(unittest.TestCase):
    def compare(self, out: Path, mode: str):
        full, cutoff = demo.write_case(out, mode)
        return replay.run(full, cutoff, ["entry_date"], ["symbol"],
                          "included", None, 0.0, 1e-9, "pnl")

    def test_clean_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.compare(Path(tmp) / "clean", "clean")
            self.assertTrue(result["summary"]["identical"])
            self.assertEqual(result["summary"]["added_total"], 0)

    def test_leaky_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.compare(Path(tmp) / "leaky", "leaky")
            summary = result["summary"]
            self.assertFalse(summary["identical"])
            self.assertEqual(summary["groups"], 2)
            self.assertEqual(summary["added_total"], 4)
            self.assertEqual(summary["removed_total"], 0)
            self.assertEqual(summary["added_pnl"], 700.0)

    def test_cutoff_keeps_all_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, cutoff = demo.write_case(Path(tmp) / "case", "leaky")
            frame = pd.read_csv(cutoff)
            self.assertTrue((frame["included"] == 1).all())

    def test_leaky_filter_values(self):
        rows = {row["symbol"]: row["included"] for row in demo.build_rows("leaky")}
        self.assertEqual(rows["000002"], 1)  # 在区间内
        self.assertEqual(rows["000001"], 0)  # 太浅
        self.assertEqual(rows["000003"], 0)  # 太深

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                clean = demo.main(["--out", str(Path(tmp) / "cli-clean"), "--mode", "clean"])
                leaky = demo.main(["--out", str(Path(tmp) / "cli-leaky"), "--mode", "leaky"])
            self.assertEqual(clean, 0)
            self.assertEqual(leaky, 1)


if __name__ == "__main__":
    unittest.main()