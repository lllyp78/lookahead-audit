#!/usr/bin/env python3
"""注入 runner 示例：把「命令行回测 + 指标文件」接到注入框架上。

这是**模板**，不是一个能直接跑的项目适配器。下方注入锚点是虚构写法，
不对应任何真实策略；用的时候把下面三处换掉：

  1. ``BACKTEST_CMD`` —— 你自己的回测命令
  2. ``run()`` 里读指标的部分 —— 指标文件 / stdout 解析
  3. ``injection_points()`` —— 你的策略里那些"数据边界"所在的位置

配套运行：

    python3 inject_test.py --runner injection_runner_example.py \
        --metric annualized_return --diagnostics executed_trade_count,avg_position_count

本示例保存文件原始字节，再做一次文本替换。恢复时只在文件仍是本次注入结果时
写回原始字节；若其他进程同时改了文件，立即报错，避免覆盖别人的改动。

**patch() 改了什么，restore() 必须还原回去**；框架在 finally 里调用恢复。
若恢复失败，立即停止试验并检查文件，不要继续下一个注入点。
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inject_test import InjectionSpec  # noqa: E402

# ── 换这里 ①：你的回测命令 ────────────────────────────────────────────────
PROJECT_ROOT = Path("/path/to/your/project")
SOURCE_FILE = PROJECT_ROOT / "your_engine.py"
BACKTEST_CMD = [
    sys.executable, "-m", "your_package.run",
    "--start", "2020-01-01", "--end", "2024-12-31",
]


def _read_metrics(output_dir: Path) -> dict[str, float]:
    """读回测产物。本示例读 metrics.csv(name,value) 与 trades.csv 的笔数。"""
    metrics: dict[str, float] = {}
    with (output_dir / "metrics.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            metrics[row["name"]] = float(row["value"])
    with (output_dir / "trades.csv").open(encoding="utf-8") as handle:
        executed = sum(1 for row in csv.DictReader(handle) if row["status"] == "executed")
    metrics["executed_trade_count_rows"] = float(executed)
    return metrics


# ── 换这里 ②：run() 必须返回指标字典 ─────────────────────────────────────
def run() -> dict[str, float]:
    """跑一次回测，返回指标。框架会静默掉 stdout。"""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [*BACKTEST_CMD, "--output", tmp],
            cwd=PROJECT_ROOT, check=True, capture_output=True, text=True,
        )
        return _read_metrics(Path(tmp))


# ── 换这里 ③：你的注入点 ─────────────────────────────────────────────────
# 在自己的隔离副本中运行回测更稳妥；此辅助保留适配现有项目的最小用法。


def _text_patch(old: str, new: str):
    original_bytes: bytes | None = None
    patched_bytes: bytes | None = None

    def check_regular_file() -> None:
        if SOURCE_FILE.is_symlink():
            raise ValueError(f"拒绝原子替换符号链接：{SOURCE_FILE}")
        if SOURCE_FILE.stat().st_nlink != 1:
            raise ValueError(f"拒绝原子替换多重硬链接文件：{SOURCE_FILE}")

    def write_bytes(value: bytes) -> None:
        """同目录原子替换，保留文件权限。"""
        check_regular_file()
        mode = SOURCE_FILE.stat().st_mode
        with tempfile.NamedTemporaryFile(dir=SOURCE_FILE.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(value)
        try:
            os.chmod(temporary, mode)
            os.replace(temporary, SOURCE_FILE)
        finally:
            temporary.unlink(missing_ok=True)

    def apply() -> None:
        nonlocal original_bytes, patched_bytes
        if original_bytes is not None:
            raise RuntimeError("已有注入尚未恢复")
        check_regular_file()
        original_bytes = SOURCE_FILE.read_bytes()
        original = original_bytes.decode("utf-8")
        if old not in original:
            original_bytes = None
            raise AssertionError(f"锚点未命中，注入点需要按当前代码更新：{old[:60]}")
        patched_bytes = original.replace(old, new, 1).encode("utf-8")
        write_bytes(patched_bytes)

    def restore() -> None:
        nonlocal original_bytes, patched_bytes
        if original_bytes is None:
            return
        check_regular_file()
        current = SOURCE_FILE.read_bytes()
        if current == patched_bytes:
            write_bytes(original_bytes)
        elif current != original_bytes:
            raise RuntimeError(f"注入后文件又被修改，拒绝覆盖：{SOURCE_FILE}")
        original_bytes = None
        patched_bytes = None

    return apply, restore


def injection_points() -> list[InjectionSpec]:
    specs: list[InjectionSpec] = []

    # 示例一：把某一条信号路径的输入边界提前一根 bar。以下代码是虚构占位符。
    apply, restore = _text_patch(
        "signal_features = feed.read_as_of(previous_bar_end)",
        "signal_features = feed.read_as_of(next_bar_end)  # INJECT",
    )
    specs.append(InjectionSpec("信号输入越界", apply, restore, "A1",
                               "仅将该信号输入的可用时间边界提前一根 bar"))

    # 示例二：独立的风控输入路径；实际项目须验证没有连带更改订单执行输入。
    apply, restore = _text_patch(
        "risk_features = feed.read_as_of(risk_cutoff)",
        "risk_features = feed.read_as_of(later_risk_cutoff)  # INJECT",
    )
    specs.append(InjectionSpec("风控输入越界", apply, restore, "C4",
                               "仅改变风控输入时间，检查订单路径是否仍独立"))

    # ⚠️ 若某节点**无法单变量注入**（例如信号快照与执行快照是两个显式变量、
    #    被多条路径共用），不要硬做整体替换——那多半会掐死某条路径，
    #    得到不可解释的 Δ。改为在第二层用溯源论证，并在报告里写明原因。
    return specs


if __name__ == "__main__":
    specs = injection_points()
    print(f"本示例定义了 {len(specs)} 个注入点：")
    for spec in specs:
        print(f"  - {spec.name}（{spec.category}）：{spec.note}")
    print("\n用 inject_test.py --runner 本文件 运行；或先 --selftest 验证框架本身。")
