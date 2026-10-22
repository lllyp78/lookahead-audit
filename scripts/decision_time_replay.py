#!/usr/bin/env python3
"""比较“全量数据排序”与“仅 selection_at 前可用数据排序”的决策产物。

用途：预计算信号/排序文件把未来数据算进排序时，截断回测终点通常发现不了；
本脚本按组比较两套排序的 top-k、top-1、共同键相关性，以及缺失/新增键。

退出码：0 所测组 top-k 完全一致且没有缺失/新增键；1 有差异；2 输入无效。

注意：
- 一致只说明所测组、所测 top-k 在两种输入下相同，不证明没有泄漏。
- 差异先排除 cutoff 实现、数据版本、缺失行和并列处理，再归因。
- 并列按 key 列升序稳定排序；真实引擎的 tie-break 可能不同，本脚本是诊断，不是引擎回放。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

Key = Tuple[str, ...]


def split_columns(value: str) -> List[str]:
    columns = [part.strip() for part in value.split(",") if part.strip()]
    if not columns:
        raise ValueError("列名为空")
    return columns


def key_of(row: pd.Series, columns: Sequence[str]) -> Key:
    return tuple(str(row[column]) for column in columns)


def key_label(key: Key) -> str:
    return "|".join(key)


def load_table(path: Path, group_columns: Sequence[str], key_columns: Sequence[str],
               rank_column: str) -> pd.DataFrame:
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = list(dict.fromkeys(list(group_columns) + list(key_columns) + [rank_column]))
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{path} 缺少列：{missing}")
    if frame.empty:
        raise ValueError(f"{path} 没有记录")
    frame["_rank"] = pd.to_numeric(frame[rank_column], errors="coerce")
    if frame["_rank"].isna().any():
        bad = frame.loc[frame["_rank"].isna(), rank_column].head(3).tolist()
        raise ValueError(f"{path} 的 {rank_column} 含非数值：{bad}")
    duplicated = frame.duplicated(list(dict.fromkeys(list(group_columns) + list(key_columns))))
    if duplicated.any():
        raise ValueError(f"{path} 在 group+key 上不唯一")
    return frame


def grouped(frame: pd.DataFrame, group_columns: Sequence[str]) -> Dict[Key, pd.DataFrame]:
    result: Dict[Key, pd.DataFrame] = {}
    for key, sub in frame.groupby(list(group_columns), sort=False):
        if not isinstance(key, tuple):
            key = (key,)
        result[tuple(str(part) for part in key)] = sub
    return result


def ranked(frame: pd.DataFrame, key_columns: Sequence[str], ascending: bool) -> pd.DataFrame:
    # 并列先按 key 升序；显式 tie-break 让结果可复现。
    return frame.sort_values(["_rank"] + list(key_columns),
                             ascending=[ascending] + [True] * len(key_columns))


def top_keys(frame: pd.DataFrame, key_columns: Sequence[str], top_k: int,
             ascending: bool) -> List[Key]:
    ordered = ranked(frame, key_columns, ascending).head(top_k)
    return [key_of(row, key_columns) for _, row in ordered.iterrows()]


def spearman(full: pd.DataFrame, cutoff: pd.DataFrame, key_columns: Sequence[str]) -> Optional[float]:
    left = pd.Series(full["_rank"].to_numpy(),
                     index=[key_of(row, key_columns) for _, row in full.iterrows()])
    right = pd.Series(cutoff["_rank"].to_numpy(),
                      index=[key_of(row, key_columns) for _, row in cutoff.iterrows()])
    common = left.index.intersection(right.index)
    if len(common) < 2:
        return None
    a = left.loc[common].rank()
    b = right.loc[common].rank()
    if a.nunique() < 2 or b.nunique() < 2:
        return None
    return float(a.corr(b))


def compare_groups(full: pd.DataFrame, cutoff: pd.DataFrame,
                   group_columns: Sequence[str], key_columns: Sequence[str],
                   top_k: int, ascending: bool) -> Dict[str, object]:
    full_groups = grouped(full, group_columns)
    cutoff_groups = grouped(cutoff, group_columns)
    rows: List[Dict[str, object]] = []
    empty = full.iloc[0:0]
    for group in sorted(set(full_groups) | set(cutoff_groups)):
        left = full_groups.get(group, empty)
        right = cutoff_groups.get(group, empty)
        left_top = top_keys(left, key_columns, top_k, ascending) if len(left) else []
        right_top = top_keys(right, key_columns, top_k, ascending) if len(right) else []
        left_set, right_set = set(left_top), set(right_top)
        left_all = {key_of(row, key_columns) for _, row in left.iterrows()}
        right_all = {key_of(row, key_columns) for _, row in right.iterrows()}
        missing = sorted(left_all - right_all)
        extra = sorted(right_all - left_all)
        union = left_set | right_set
        rows.append({
            "group": "|".join(group),
            "full_n": int(len(left)), "cutoff_n": int(len(right)),
            "full_top": [key_label(key) for key in left_top],
            "cutoff_top": [key_label(key) for key in right_top],
            "top1_match": bool(left_top and right_top and left_top[0] == right_top[0]),
            "topk_match": bool(left_top == right_top),
            "overlap": int(len(left_set & right_set)),
            "jaccard": float(len(left_set & right_set) / len(union)) if union else 1.0,
            "missing_in_cutoff": len(missing),
            "missing_examples": [key_label(key) for key in missing[:20]],
            "extra_in_cutoff": len(extra),
            "extra_examples": [key_label(key) for key in extra[:20]],
            "spearman": spearman(left, right, key_columns) if len(left) and len(right) else None,
        })
    top1_changed = sum(1 for row in rows if not row["top1_match"])
    topk_changed = sum(1 for row in rows if not row["topk_match"])
    missing_total = sum(int(row["missing_in_cutoff"]) for row in rows)
    extra_total = sum(int(row["extra_in_cutoff"]) for row in rows)
    jaccards = [float(row["jaccard"]) for row in rows]
    summary = {
        "groups": len(rows),
        "top1_changed": top1_changed,
        "topk_changed": topk_changed,
        "missing_total": missing_total,
        "extra_total": extra_total,
        "mean_jaccard": float(sum(jaccards) / len(jaccards)) if jaccards else None,
        "identical": bool(rows) and not topk_changed and not missing_total and not extra_total,
    }
    return {"summary": summary, "groups": rows}


def run(full_path: Path, cutoff_path: Path, group_columns: Sequence[str],
        key_columns: Sequence[str], rank_column: str, top_k: int,
        ascending: bool) -> Dict[str, object]:
    if top_k < 1:
        raise ValueError("--top-k 必须 >= 1")
    full = load_table(full_path, group_columns, key_columns, rank_column)
    cutoff = load_table(cutoff_path, group_columns, key_columns, rank_column)
    return compare_groups(full, cutoff, group_columns, key_columns, top_k, ascending)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", required=True, type=Path, help="全量数据的决策 CSV")
    parser.add_argument("--cutoff", required=True, type=Path, help="仅 selection_at 前可用数据的决策 CSV")
    parser.add_argument("--group-columns", required=True,
                        help="分组列，逗号分隔，例如 trade_date")
    parser.add_argument("--key-columns", required=True,
                        help="唯一键列，逗号分隔，例如 trade_date,symbol")
    parser.add_argument("--rank-column", required=True, help="排序数值列，例如 score")
    parser.add_argument("--top-k", type=int, default=2, help="比较每组 top-k（默认 2）")
    parser.add_argument("--ascending", action="store_true", help="数值越小越优先（默认越大越优先）")
    parser.add_argument("--out", type=Path, help="可选 JSON 输出路径")
    args = parser.parse_args(argv)

    try:
        result = run(args.full, args.cutoff, split_columns(args.group_columns),
                     split_columns(args.key_columns), args.rank_column,
                     args.top_k, args.ascending)
    except (ValueError, OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        print(f"无法完成决策时点重放：{exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(f"groups={summary['groups']} top1_changed={summary['top1_changed']} "
          f"topk_changed={summary['topk_changed']} missing={summary['missing_total']} "
          f"extra={summary['extra_total']} mean_jaccard={summary['mean_jaccard']}")
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if summary["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())