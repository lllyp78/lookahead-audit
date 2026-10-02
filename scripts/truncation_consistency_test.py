#!/usr/bin/env python3
"""比较不同截止日的回测前缀；差异是待调查线索，匹配不证明无泄漏。

默认只使用新目录，避免把先前代码、配置或数据产生的 CSV 当作本次结果。
退出码：0 所测前缀一致；1 发现差异；2 无法完成有效检验。
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, Sequence

import pandas as pd


def run_backtest(command: str, output: Path, start: str, end: str, cwd: str | None) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"输出目录已有内容，拒绝复用旧结果：{output}")
    output.mkdir(parents=True, exist_ok=True)
    # 先拆命令再替换占位符，路径中有空格也能作为一个参数传给回测程序。
    argv = [part.format(start=start, end=end, output=str(output))
            for part in shlex.split(command)]
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"回测失败（end={end}，退出码 {result.returncode}）：\n"
                           f"{result.stdout[-1500:]}\n{result.stderr[-1500:]}")


def load_files(path: Path, patterns: Sequence[str], date_column: str,
               strip_regex: str, strip_columns: Sequence[str]) -> Dict[str, pd.DataFrame]:
    files: Dict[str, pd.DataFrame] = {}
    for index, pattern in enumerate(patterns):
        matches = sorted(p for p in path.glob(pattern) if p.is_file())
        if not matches:
            raise ValueError(f"{path} 下没有匹配 {pattern!r} 的文件")
        for matched in matches:
            key = str(matched.relative_to(path))
            if key in files:
                raise ValueError(f"文件被多个 --files 模式重复匹配：{key}")
            frame = pd.read_csv(matched, dtype=str, keep_default_na=False)
            if date_column not in frame:
                raise ValueError(f"{matched} 缺少日期列 {date_column!r}")
            if index == 0 and frame.empty:
                raise ValueError(f"主要比较文件没有任何记录：{matched}")
            for column in strip_columns:
                if column not in frame:
                    raise ValueError(f"{matched} 缺少待清理列 {column!r}")
                frame[column] = frame[column].str.replace(strip_regex, "<VOLATILE>", regex=True)
            files[key] = frame
    return files


def prepared(frame: pd.DataFrame, date_column: str, key_columns: Sequence[str],
             start: str, end: str) -> pd.DataFrame:
    missing = set(key_columns) - set(frame.columns)
    if missing:
        raise ValueError(f"缺少主键列：{sorted(missing)}")
    dates = pd.to_datetime(frame[date_column], utc=True, errors="raise")
    if dates.isna().any():
        raise ValueError(f"{date_column} 包含空日期")
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    result = frame.loc[(dates >= lower) & (dates < upper)].copy()
    if result.duplicated(list(key_columns)).any():
        raise ValueError(f"比较区间内主键不唯一：{','.join(key_columns)}")
    return result.sort_values(list(key_columns)).reset_index(drop=True)


def compare(full: pd.DataFrame, short: pd.DataFrame, *, start: str, end: str,
            date_column: str, key_columns: Sequence[str]) -> str | None:
    left = prepared(full, date_column, key_columns, start, end)
    right = prepared(short, date_column, key_columns, start, end)
    if left.empty and right.empty:
        return None
    if set(left.columns) != set(right.columns):
        return f"列集合不同：全期={sorted(left.columns)} 截断={sorted(right.columns)}"
    if len(left) != len(right):
        return f"重叠区间行数不同：全期={len(left)} 截断={len(right)}"
    columns = [*key_columns, *sorted(set(left.columns) - set(key_columns))]
    for column in columns:
        different = left[column] != right[column]
        if different.any():
            row = int(different.idxmax())
            return (f"首个分歧：行={row} 列={column} "
                    f"全期={left.at[row, column]!r} 截断={right.at[row, column]!r}")
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回测截断前缀一致性检验")
    parser.add_argument("--command", required=True, help="命令模板，含 {end}、{output}，可用 {start}")
    parser.add_argument("--output-template", required=True, help="新输出目录模板，必须含 {end}")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--ends", required=True, help="升序逗号分隔的 ISO 日期；末项为全期")
    parser.add_argument("--files", default="daily_nav.csv", help="逗号分隔的文件或 glob；首项必须有记录")
    parser.add_argument("--date-column", default="trade_date")
    parser.add_argument("--key-columns", default="trade_date", help="逗号分隔；多资产常用 trade_date,code")
    parser.add_argument("--strip-regex", default="", help="仅作用于 --strip-columns 指定列")
    parser.add_argument("--strip-columns", default="", help="明确列出需忽略易变标记的文本列")
    parser.add_argument("--cwd", help="回测命令的工作目录")
    args = parser.parse_args(argv)

    ends = [part.strip() for part in args.ends.split(",") if part.strip()]
    patterns = [part.strip() for part in args.files.split(",") if part.strip()]
    keys = [part.strip() for part in args.key_columns.split(",") if part.strip()]
    strip_columns = [part.strip() for part in args.strip_columns.split(",") if part.strip()]
    if len(ends) < 2 or not patterns or not keys or "{end}" not in args.output_template:
        parser.error("需要至少两个截止日、一个比较文件和主键列；输出模板须含 {end}")
    if bool(args.strip_regex) != bool(strip_columns):
        parser.error("--strip-regex 和 --strip-columns 必须同时指定")
    try:
        dates = [pd.Timestamp(value) for value in ends]
        if any(item.strftime("%Y-%m-%d") != raw for item, raw in zip(dates, ends)):
            raise ValueError("--ends 必须是 YYYY-MM-DD 日期")
        if dates != sorted(set(dates)):
            raise ValueError("--ends 必须严格升序且不重复")
        pd.Timestamp(args.start)
        if strip_columns:
            re.compile(args.strip_regex)

        runs: Dict[str, Dict[str, pd.DataFrame]] = {}
        primary_files: Dict[str, set[str]] = {}
        for end in ends:
            output = Path(args.output_template.format(end=end))
            print(f"[跑] end={end} → {output}", flush=True)
            run_backtest(args.command, output, args.start, end, args.cwd)
            runs[end] = load_files(output, patterns, args.date_column,
                                   args.strip_regex, strip_columns)
            primary_files[end] = {
                str(path.relative_to(output)) for path in output.glob(patterns[0]) if path.is_file()
            }
            cutoff = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
            for name, frame in runs[end].items():
                dates_in_file = pd.to_datetime(frame[args.date_column], utc=True, errors="raise")
                if dates_in_file.isna().any():
                    raise ValueError(f"{name} 包含空日期")
                if (dates_in_file >= cutoff).any():
                    raise ValueError(f"{name} 的 {end} 运行包含截止日之后的数据")

        all_files = set(runs[ends[-1]])
        failures = 0
        for end in ends[:-1]:
            if set(runs[end]) != all_files:
                raise ValueError(f"{end} 的实际文件清单与全期不同："
                                 f"{sorted(set(runs[end]) ^ all_files)}")
            for name in sorted(all_files):
                full = runs[ends[-1]][name]
                short = runs[end][name]
                full_dates = pd.to_datetime(full[args.date_column], utc=True, errors="raise")
                short_dates = pd.to_datetime(short[args.date_column], utc=True, errors="raise")
                if name in primary_files[ends[-1]] and full_dates.max() <= short_dates.max():
                    raise ValueError(f"{name} 全期没有比 {end} 更晚的数据，无法检验截断")
                problem = compare(full, short, start=args.start, end=end,
                                  date_column=args.date_column, key_columns=keys)
                if problem:
                    failures += 1
                    print(f"[差异] {name} end={end}: {problem}")
                else:
                    print(f"[一致] {name} end={end}: 所测重叠区间一致")
        if failures:
            print(f"判定：{failures} 项前缀不一致；先排查配置、状态与数据版本，再定位泄漏。")
            return 1
        print("判定：所测前缀一致；未覆盖分支及上游数据版本仍需单独审查。")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, pd.errors.ParserError) as exc:
        print(f"无法判定：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
