#!/usr/bin/env python3
"""合成价格的最小回测：clean 用已完成日线，leaky 用全期均值。

决策设在每日 bar 完成之后，信号在下一日才影响净值。两次不同截止日
的 clean 前缀相同；leaky 在较早日期的信号会随截止日改变。
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

PRICES = [
    ("2020-01-01", 100.0),
    ("2020-01-02", 105.0),
    ("2020-01-03", 101.0),
    ("2020-01-04", 110.0),
    ("2020-01-05", 98.0),
    ("2020-01-06", 120.0),
]


def run(start: str, end: str, output: Path, mode: str) -> None:
    bars = [(day, close) for day, close in PRICES if start <= day <= end]
    if not bars:
        raise ValueError("所选日期没有样本")
    whole_mean = sum(close for _, close in bars) / len(bars)
    output.mkdir(parents=True, exist_ok=True)
    nav = 1.0
    previous_signal = 0
    previous_close = None
    with (output / "daily_nav.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["trade_date", "code", "signal", "nav"])
        for index, (day, close) in enumerate(bars):
            if previous_close is not None:
                nav *= 1 + previous_signal * (close / previous_close - 1)
            available_mean = sum(price for _, price in bars[:index + 1]) / (index + 1)
            # clean：收盘后根据当日已完成 bar 决策；下一日才计入该信号。
            # leaky：用本次回测终点之前的全部收盘价判断更早日期。
            reference = whole_mean if mode == "leaky" else available_mean
            signal = int(close > reference)
            writer.writerow([day, "DEMO", signal, f"{nav:.8f}"])
            previous_signal, previous_close = signal, close


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("clean", "leaky"), default="clean")
    args = parser.parse_args()
    run(args.start, args.end, args.output, args.mode)


if __name__ == "__main__":
    main()
