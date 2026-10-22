#!/usr/bin/env python3
"""合成例子：执行层的窗口量进入排序，形成未来函数。

clean：9:25 只用 auction_amount 排序。
leaky：full 排序用 auction_amount + window_amount；cutoff 仍只用 auction_amount。

window_amount 是首 30 分钟（如 9:30–10:00，按数据源 bar 标签）的成交额，10:00
才完整；9:25 做选择时不可得。clean 的 full 与 cutoff 完全相同；leaky 的 full
在若干组换掉 top-1/top-k，`decision_time_replay.py` 应报出差异。

用法：
    python3 examples/demo_execution_rank_leak.py --out /tmp/rank-leak --mode leaky

退出码：clean 0（两侧一致）；leaky 1（检测到 top-k 差异）；输入无效 2。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import decision_time_replay as replay  # noqa: E402

CASES = [
    ("2024-01-02", "000001", 100_000, 900_000),
    ("2024-01-02", "000002", 300_000, 100_000),
    ("2024-01-02", "000003", 50_000, 500_000),
    ("2024-01-03", "000004", 200_000, 50_000),
    ("2024-01-03", "000005", 180_000, 200_000),
    ("2024-01-03", "000006", 10_000, 400_000),
]


def build_rows(mode: str):
    if mode not in ("clean", "leaky"):
        raise ValueError(f"未知模式：{mode}")
    rows = []
    for day, symbol, auction, window in CASES:
        rank = auction if mode == "clean" else auction + window
        rows.append({
            "entry_date": day,
            "symbol": symbol,
            "auction_amount": auction,
            "window_amount": window,
            "score": rank,
        })
    return rows


def write_case(out: Path, mode: str):
    out.mkdir(parents=True, exist_ok=True)
    full_path, cutoff_path = out / "full.csv", out / "cutoff.csv"
    pd.DataFrame(build_rows(mode)).to_csv(full_path, index=False)
    # cutoff 是 9:25 可见的口径，始终只用竞价额。
    pd.DataFrame(build_rows("clean")).to_csv(cutoff_path, index=False)
    return full_path, cutoff_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="输出目录")
    parser.add_argument("--mode", choices=("clean", "leaky"), default="leaky")
    parser.add_argument("--top-k", type=int, default=2)
    args = parser.parse_args(argv)

    try:
        full, cutoff = write_case(args.out, args.mode)
        result = replay.run(full, cutoff, ["entry_date"], ["symbol"],
                            "score", args.top_k, False)
    except (ValueError, OSError) as exc:
        print(f"demo 失败：{exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(f"mode={args.mode} groups={summary['groups']} "
          f"top1_changed={summary['top1_changed']} topk_changed={summary['topk_changed']} "
          f"missing={summary['missing_total']} extra={summary['extra_total']} "
          f"mean_jaccard={summary['mean_jaccard']}")
    print(f"full:   {full}")
    print(f"cutoff: {cutoff}")
    return 0 if summary["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())