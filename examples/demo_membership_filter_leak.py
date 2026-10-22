#!/usr/bin/env python3
"""合成例子：候选过滤使用 T+1 开盘价，形成候选 membership 的未来函数。

clean：full 与 cutoff 都包含全部 T 日候选（T 收盘无法知道 T+1 开盘缺口）。
leaky：full 用 T+1 开盘缺口区间过滤；cutoff 仍包含全部 T 日候选。

leaky 的 full 会剔除一些候选，而 cutoff 版本保留它们；`membership_time_replay.py`
应报出 added（cutoff 相对 full 新增）及其 PnL。clean 应完全一致。

用法：
    python3 examples/demo_membership_filter_leak.py --out /tmp/membership-leak --mode leaky

退出码：clean 0（一致）；leaky 1（检测到 membership 差异）；输入无效 2。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import membership_time_replay as replay  # noqa: E402

CASES = [
    ("2024-01-02", "000001", -0.01, 1200.0),
    ("2024-01-02", "000002", -0.03, 3000.0),
    ("2024-01-02", "000003", -0.10, -800.0),
    ("2024-01-03", "000004", -0.04, 1500.0),
    ("2024-01-03", "000005", -0.01, -600.0),
    ("2024-01-03", "000006", -0.08, 900.0),
]


def build_rows(mode: str):
    if mode not in ("clean", "leaky"):
        raise ValueError(f"未知模式：{mode}")
    rows = []
    for day, symbol, gap, pnl in CASES:
        included = 1
        if mode == "leaky" and not (-0.05 <= gap <= -0.02):
            included = 0
        rows.append({
            "entry_date": day,
            "symbol": symbol,
            "gap": gap,
            "included": included,
            "pnl": pnl,
        })
    return rows


def write_case(out: Path, mode: str):
    out.mkdir(parents=True, exist_ok=True)
    full_path, cutoff_path = out / "full.csv", out / "cutoff.csv"
    pd.DataFrame(build_rows(mode)).to_csv(full_path, index=False)
    # cutoff 是 T 收盘可见口径：不知道 T+1 开盘缺口，保留全部候选。
    pd.DataFrame(build_rows("clean")).to_csv(cutoff_path, index=False)
    return full_path, cutoff_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="输出目录")
    parser.add_argument("--mode", choices=("clean", "leaky"), default="leaky")
    args = parser.parse_args(argv)

    try:
        full, cutoff = write_case(args.out, args.mode)
        result = replay.run(full, cutoff, ["entry_date"], ["symbol"],
                            "included", None, 0.0, 1e-9, "pnl")
    except (ValueError, OSError) as exc:
        print(f"demo 失败：{exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(f"mode={args.mode} groups={summary['groups']} added={summary['added_total']} "
          f"removed={summary['removed_total']} added_pnl={summary['added_pnl']:.2f}")
    print(f"full:   {full}")
    print(f"cutoff: {cutoff}")
    return 0 if summary["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())