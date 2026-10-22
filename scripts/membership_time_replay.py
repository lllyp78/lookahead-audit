#!/usr/bin/env python3
"""比较全量与 selection_at 截断口径的候选 membership / 权重。

full：使用未来数据的版本；cutoff：只用 selection_at 前可用数据的版本。
按组比较 included 集合、weight 变化，以及可选 PnL 的影响。

退出码：0 无差异；1 有差异；2 输入无效。

注意：
- membership=0 与“该行不存在”都表示未入选，不算差异。
- weight 模式下，入选 = weight > --weight-threshold；一端入选、另一端未入选算新增/剔除，不算权重变化。
- 并列/缺失处理需在 cutoff 实现中单独核对；本脚本只比较给定的两份产物。
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
               value_column: str, kind: str, threshold: float,
               pnl_column: Optional[str]) -> pd.DataFrame:
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = list(dict.fromkeys(list(group_columns) + list(key_columns) + [value_column]
                                  + ([pnl_column] if pnl_column else [])))
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{path} 缺少列：{missing}")
    if frame.empty:
        raise ValueError(f"{path} 没有记录")

    frame["_value"] = pd.to_numeric(frame[value_column], errors="coerce")
    if frame["_value"].isna().any():
        bad = frame.loc[frame["_value"].isna(), value_column].head(3).tolist()
        raise ValueError(f"{path} 的 {value_column} 含非数值：{bad}")
    if kind == "membership":
        if not frame["_value"].isin([0, 1]).all():
            raise ValueError(f"{path} 的 membership 必须为 0/1")
        frame["_included"] = frame["_value"].astype(bool)
        frame["_weight"] = frame["_included"].astype(float)
    else:
        if (frame["_value"] < 0).any():
            raise ValueError(f"{path} 的 weight 不能为负")
        frame["_weight"] = frame["_value"]
        frame["_included"] = frame["_weight"] > threshold

    if pnl_column:
        frame["_pnl"] = pd.to_numeric(frame[pnl_column], errors="coerce").fillna(0.0)
    else:
        frame["_pnl"] = 0.0

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


def compare(full: pd.DataFrame, cutoff: pd.DataFrame, group_columns: Sequence[str],
            key_columns: Sequence[str], kind: str, tolerance: float,
            pnl_column: Optional[str]) -> Dict[str, object]:
    full_groups = grouped(full, group_columns)
    cutoff_groups = grouped(cutoff, group_columns)
    empty = full.iloc[0:0]
    rows: List[Dict[str, object]] = []
    for group in sorted(set(full_groups) | set(cutoff_groups)):
        left = full_groups.get(group, empty)
        right = cutoff_groups.get(group, empty)
        left_map = {key_of(row, key_columns): (bool(row["_included"]), float(row["_weight"]), float(row["_pnl"]))
                    for _, row in left.iterrows()}
        right_map = {key_of(row, key_columns): (bool(row["_included"]), float(row["_weight"]), float(row["_pnl"]))
                     for _, row in right.iterrows()}
        keys = set(left_map) | set(right_map)
        zero = (False, 0.0, 0.0)
        added = sorted(key for key in keys
                       if right_map.get(key, zero)[0] and not left_map.get(key, zero)[0])
        removed = sorted(key for key in keys
                         if left_map.get(key, zero)[0] and not right_map.get(key, zero)[0])
        changed = []
        if kind == "weight":
            for key in set(left_map) & set(right_map):
                if left_map[key][0] and right_map[key][0]:
                    if abs(left_map[key][1] - right_map[key][1]) > tolerance:
                        changed.append((key, left_map[key][1], right_map[key][1]))
            changed.sort(key=lambda item: item[0])
        rows.append({
            "group": "|".join(group),
            "full_rows": int(len(left)), "cutoff_rows": int(len(right)),
            "full_included": int(sum(value[0] for value in left_map.values())),
            "cutoff_included": int(sum(value[0] for value in right_map.values())),
            "added": len(added),
            "added_examples": [key_label(key) for key in added[:20]],
            "removed": len(removed),
            "removed_examples": [key_label(key) for key in removed[:20]],
            "weight_changed": len(changed),
            "weight_changed_examples": [
                {"key": key_label(key), "full": full_weight, "cutoff": cutoff_weight}
                for key, full_weight, cutoff_weight in changed[:20]],
            "added_pnl": float(sum(right_map[key][2] for key in added)),
            "removed_pnl": float(sum(left_map[key][2] for key in removed)),
            "weight_changed_pnl": float(sum(right_map[key][2] for key, _, _ in changed)),
        })

    added_total = sum(int(row["added"]) for row in rows)
    removed_total = sum(int(row["removed"]) for row in rows)
    changed_total = sum(int(row["weight_changed"]) for row in rows)
    summary = {
        "groups": len(rows),
        "added_total": added_total,
        "removed_total": removed_total,
        "weight_changed_total": changed_total,
        "added_pnl": float(sum(float(row["added_pnl"]) for row in rows)),
        "removed_pnl": float(sum(float(row["removed_pnl"]) for row in rows)),
        "weight_changed_pnl": float(sum(float(row["weight_changed_pnl"]) for row in rows)),
        "pnl_column": pnl_column,
        "identical": bool(rows) and added_total == 0 and removed_total == 0 and changed_total == 0,
    }
    return {"summary": summary, "groups": rows}


def run(full_path: Path, cutoff_path: Path, group_columns: Sequence[str],
        key_columns: Sequence[str], membership_column: Optional[str],
        weight_column: Optional[str], weight_threshold: float,
        weight_tolerance: float, pnl_column: Optional[str]) -> Dict[str, object]:
    if bool(membership_column) == bool(weight_column):
        raise ValueError("--membership-column 与 --weight-column 必须二选一")
    kind = "membership" if membership_column else "weight"
    value_column = membership_column or weight_column
    full = load_table(full_path, group_columns, key_columns, value_column, kind,
                      weight_threshold, pnl_column)
    cutoff = load_table(cutoff_path, group_columns, key_columns, value_column, kind,
                        weight_threshold, pnl_column)
    return compare(full, cutoff, group_columns, key_columns, kind,
                   weight_tolerance, pnl_column)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", required=True, type=Path, help="全量/含未来数据的决策 CSV")
    parser.add_argument("--cutoff", required=True, type=Path, help="仅 selection_at 前可用数据的决策 CSV")
    parser.add_argument("--group-columns", required=True, help="分组列，逗号分隔，例如 entry_date")
    parser.add_argument("--key-columns", required=True, help="唯一键列，逗号分隔，例如 symbol")
    parser.add_argument("--membership-column", help="0/1 入选列；与 --weight-column 二选一")
    parser.add_argument("--weight-column", help="数值权重列；与 --membership-column 二选一")
    parser.add_argument("--weight-threshold", type=float, default=0.0,
                        help="weight 模式下视为入选的阈值（默认 0）")
    parser.add_argument("--weight-tolerance", type=float, default=1e-9,
                        help="weight 变化的最小绝对差（默认 1e-9）")
    parser.add_argument("--pnl-column", help="可选的损益列；用于汇总新增/剔除/调权的 PnL")
    parser.add_argument("--out", type=Path, help="可选 JSON 输出路径")
    args = parser.parse_args(argv)

    try:
        result = run(args.full, args.cutoff, split_columns(args.group_columns),
                     split_columns(args.key_columns), args.membership_column,
                     args.weight_column, args.weight_threshold,
                     args.weight_tolerance, args.pnl_column)
    except (ValueError, OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        print(f"无法完成 membership 重放：{exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(f"groups={summary['groups']} added={summary['added_total']} "
          f"removed={summary['removed_total']} weight_changed={summary['weight_changed_total']} "
          f"added_pnl={summary['added_pnl']:.2f} removed_pnl={summary['removed_pnl']:.2f} "
          f"weight_changed_pnl={summary['weight_changed_pnl']:.2f}")
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if summary["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())