"""使用临时文件与合成回测验证真实行为，不依赖私有策略数据。"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import inject_test as injection
import injection_runner_example as example
import scan_adjustment_caliber as adjustment
import truncation_consistency_test as truncation


FAKE_BACKTEST = '''\
import argparse
import csv
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--end", required=True)
p.add_argument("--output", required=True)
p.add_argument("--mode", default="stable")
a = p.parse_args()
out = Path(a.output)
out.mkdir(parents=True, exist_ok=True)
dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
dates = [day for day in dates if day <= a.end]
if a.mode == "no-extension":
    dates = dates[:1]
if a.mode == "future-full" and a.end == "2020-01-03":
    dates.append("2020-01-04")
with (out / "daily_nav.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["trade_date", "code", "nav"])
    for day in dates:
        if a.mode == "empty":
            continue
        value = "2" if a.mode == "diverge" and a.end == "2020-01-03" and day == "2020-01-01" else "1"
        code = "abcdefabcdef" if a.end == "2020-01-01" else "123456789abc"
        if a.mode != "codes":
            code = "DEMO"
        writer.writerow([day, code, value])
if a.mode == "two-files":
    sub = out / "extra"
    sub.mkdir()
    with (sub / "daily_nav.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["trade_date", "code", "nav"])
        for day in dates:
            value = "9" if a.end == "2020-01-03" and day == "2020-01-01" else "1"
            writer.writerow([day, "DEMO", value])
'''


class InjectionTests(unittest.TestCase):
    def test_numpy_metrics_are_valid_numbers(self):
        runner = injection.InjectionRunner(
            lambda: {"return": np.float64(1.5), "trades": np.int64(2)},
            metric="return", diagnostics=("trades",))
        self.assertEqual(runner.baseline()["trades"], 2)

    def test_refuses_symlink_source_before_patching(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.py"
            target.write_text("MODE = 0\n")
            link = Path(tmp) / "engine.py"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("当前系统不允许创建测试用符号链接")
            old_file = example.SOURCE_FILE
            example.SOURCE_FILE = link
            try:
                patch, _ = example._text_patch("MODE = 0", "MODE = 1")
                with self.assertRaisesRegex(ValueError, "符号链接"):
                    patch()
                self.assertTrue(link.is_symlink())
                self.assertEqual(target.read_text(), "MODE = 0\n")
            finally:
                example.SOURCE_FILE = old_file

    def test_refuses_hardlinked_source_before_patching(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.py"
            target.write_text("MODE = 0\n")
            link = Path(tmp) / "engine.py"
            try:
                os.link(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("当前文件系统不允许创建测试用硬链接")
            old_file = example.SOURCE_FILE
            example.SOURCE_FILE = link
            try:
                patch, _ = example._text_patch("MODE = 0", "MODE = 1")
                with self.assertRaisesRegex(ValueError, "硬链接"):
                    patch()
                self.assertEqual(target.read_text(), "MODE = 0\n")
            finally:
                example.SOURCE_FILE = old_file

    def test_preserves_preexisting_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "engine.py"
            original = b"MODE = 0\nUSER_EDIT = 42\n"
            file.write_bytes(original)
            old_file = example.SOURCE_FILE
            example.SOURCE_FILE = file
            try:
                patch, restore = example._text_patch("MODE = 0", "MODE = 1")
                patch()
                self.assertIn(b"MODE = 1", file.read_bytes())
                restore()
                self.assertEqual(file.read_bytes(), original)
            finally:
                example.SOURCE_FILE = old_file

    def test_refuses_to_overwrite_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "engine.py"
            file.write_text("MODE = 0\n")
            old_file = example.SOURCE_FILE
            example.SOURCE_FILE = file
            try:
                patch, restore = example._text_patch("MODE = 0", "MODE = 1")
                patch()
                file.write_text("MODE = 1\nNEW_EDIT = 99\n")
                with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
                    restore()
                self.assertIn("NEW_EDIT", file.read_text())
            finally:
                example.SOURCE_FILE = old_file

    def test_partial_patch_exception_still_restores(self):
        state = {"patched": False, "restored": False}

        def patch():
            state["patched"] = True
            raise RuntimeError("failed halfway")

        def restore():
            state["patched"] = False
            state["restored"] = True

        runner = injection.InjectionRunner(lambda: {"return": 1, "trades": 1},
                                           metric="return", diagnostics=("trades",))
        with self.assertRaisesRegex(RuntimeError, "failed halfway"):
            runner.inject(injection.InjectionSpec("partial", patch, restore))
        self.assertEqual(state, {"patched": False, "restored": True})

    def test_failed_restoration_stops_experiment(self):
        state = {"patched": False}
        runner = injection.InjectionRunner(
            lambda: {"return": 2 if state["patched"] else 1, "trades": 1},
            metric="return", diagnostics=("trades",))
        with self.assertRaisesRegex(RuntimeError, "恢复后基线不一致"):
            runner.inject(injection.InjectionSpec("bad", lambda: state.update(patched=True),
                                                  lambda: None))

    def test_zero_return_difference_with_trade_collapse_is_flagged(self):
        spec = injection.InjectionSpec("same", lambda: None, lambda: None)
        result = injection.InjectionResult(
            spec, {"return": 10.0, "trades": 100.0},
            {"return": 10.0, "trades": 0.0}, "return", ("trades",))
        self.assertIn("活动量塌陷", injection.verdict(result))

    def test_empty_injection_list_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Path(tmp) / "runner.py"
            runner.write_text("def run(): return {'return': 1, 'trades': 1}\n"
                              "def injection_points(): return []\n")
            with contextlib.redirect_stdout(io.StringIO()):
                result = injection.main(["--runner", str(runner), "--metric", "return",
                                         "--diagnostics", "trades"])
            self.assertEqual(result, 2)


class TruncationTests(unittest.TestCase):
    def run_case(self, tmp: str, mode: str, files: str = "daily_nav.csv") -> int:
        script = Path(tmp) / "backtest.py"
        script.write_text(FAKE_BACKTEST)
        command = f'"{sys.executable}" "{script}" --end {{end}} --output {{output}} --mode {mode}'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return truncation.main([
                "--command", command,
                "--output-template", str(Path(tmp) / "output" / "{end}"),
                "--start", "2020-01-01",
                "--ends", "2020-01-01,2020-01-03",
                "--files", files,
                "--key-columns", "trade_date,code",
            ])

    def test_identical_prefix_passes(self):
        with tempfile.TemporaryDirectory(prefix="lookahead space ") as tmp:
            self.assertEqual(self.run_case(tmp, "stable"), 0)

    def test_divergent_prefix_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_case(tmp, "diverge"), 1)

    def test_existing_results_are_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_case(tmp, "stable"), 0)
            self.assertEqual(self.run_case(tmp, "stable"), 2)

    def test_header_only_primary_file_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_case(tmp, "empty"), 2)

    def test_asset_codes_are_not_scrubbed_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_case(tmp, "codes"), 1)

    def test_glob_compares_every_matching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_case(tmp, "two-files", "**/daily_nav.csv"), 1)

    def test_future_dated_full_result_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_case(tmp, "future-full"), 2)

    def test_recursive_glob_primary_must_extend(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_case(tmp, "no-extension", "**/daily_nav.csv"), 2)

    def test_documented_clean_and_leaky_examples(self):
        demo = ROOT / "examples" / "demo_backtest.py"
        with tempfile.TemporaryDirectory() as tmp:
            for mode, expected in (("clean", 0), ("leaky", 1)):
                command = (f'"{sys.executable}" "{demo}" --start {{start}} --end {{end}} '
                           f'--output {{output}} --mode {mode}')
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = truncation.main([
                        "--command", command,
                        "--output-template", str(Path(tmp) / mode / "{end}"),
                        "--start", "2020-01-01",
                        "--ends", "2020-01-03,2020-01-06",
                        "--key-columns", "trade_date,code",
                    ])
                self.assertEqual(code, expected, mode)


class ScannerTests(unittest.TestCase):
    def kinds(self, source: str):
        return [item.kind for item in adjustment.analyze_source(Path("sample.py"), source)[0]]

    def test_direct_lookup_and_get_default(self):
        self.assertIn("cross_date_raw", self.kinds(
            'def f(current, previous):\n    return current["raw_close"] < previous["raw_close"]\n'))
        self.assertIn("cross_date_raw", self.kinds(
            'def f(current, previous):\n'
            '    x=current.get("raw_close", 0)\n'
            '    y=previous.get("raw_close", 0)\n'
            '    return x < y\n'))

    def test_reassignment_keeps_earlier_comparison(self):
        found = self.kinds(
            'def f(current, previous):\n'
            '    x=current.get("raw_close")\n'
            '    y=previous.get("raw_close")\n'
            '    flag=x < y\n'
            '    x=current.get("adj_open")\n'
            '    y=previous.get("adj_low")\n'
            '    return flag\n')
        self.assertEqual(found, ["cross_date_raw"])

    def test_same_source_raw_comparison_is_not_cross_date(self):
        self.assertEqual(self.kinds(
            'def f(current):\n'
            '    return current["raw_open"] < current["raw_low"]\n'), [])

    def test_mixed_caliber_arithmetic_is_candidate(self):
        self.assertIn("mixed_caliber", self.kinds(
            'def f(current):\n'
            '    return current["raw_close"] / current["adj_close"]\n'))

    def test_unreadable_python_is_not_reported_as_complete_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "invalid.py").write_text("def missing(\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = adjustment.main(["--root", tmp, "--json"])
            self.assertEqual(code, 2)
            self.assertIn("invalid.py", output.getvalue())


if __name__ == "__main__":
    unittest.main()
