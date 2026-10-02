#!/usr/bin/env python3
"""Python 价格口径启发式扫描：输出待核实候选，零命中不代表安全。

为什么需要它
------------
未来函数审计关注「用了哪一天的数据」；**同样致命且更容易漏掉**的是
「**价格尺度不一致**」——把不同交易日、或不同复权口径的价格放进同一个比较或比值里。

虚构的通用候选形态：

    current_price = current.get("raw_close")
    previous_price = previous.get("raw_close")
    if current_price < previous_price:
        handle_price_change()

两侧口径各自一致（都未复权），但**跨了两个交易日**：公司行为可能使价格尺度
发生变化，改变这个比较的结果。是否有问题还须核对数据源真实口径与日期。

扫描按 Python AST 的函数作用域和语句顺序追踪简单局部变量，并识别直接字段取值、
`get()` 与部分比较/算术表达式。动态字段名、跨函数数据流和复杂控制流仍需人工审查。

口径确认纪律（重要）
--------------------
本扫描**按字段名猜测口径**（`raw_*` = 未复权、`adj_*`/`*_qfq` = 复权）。
**这一推断必须到数据源核实**：有的库把复权价直接放在 `open/close/low` 列，
此时"raw 字段"其实是复权口径，跨日比较是安全的。用 `--alias-adjusted` 声明这类字段。

输出是**候选清单供人工判定**，不是结论。每条都要回答：
    (1) 这两个价格之间可能发生公司行为（除权/除息/拆股）吗？
    (2) 若发生了，结果会怎样（虚假放行 / 虚假拦截 / 假亏损）？

用法
----
    python3 scan_adjustment_caliber.py --root /path/to/project
    python3 scan_adjustment_caliber.py --root . --alias-adjusted raw_open,raw_close
    python3 scan_adjustment_caliber.py --root . --json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

CROSS_DATE_HINTS = (
    "previous", "prev", "prior", "yesterday", "signal", "last_", "old_", "entry",
)

EXTRA_RAW: Set[str] = set()
ALIAS_ADJUSTED: Set[str] = set()


def _is_raw(name: str) -> bool:
    if name in ALIAS_ADJUSTED:
        return False
    return bool(re.fullmatch(r"raw_[a-z_]+", name)) or name in EXTRA_RAW


def _is_adj(name: str) -> bool:
    if name in ALIAS_ADJUSTED:
        return True
    return bool(re.fullmatch(r"adj_[a-z_]+", name)) or bool(re.fullmatch(r"[a-z_]*_qfq", name))


@dataclass
class Binding:
    receiver: str
    field: str


def _dedupe(findings: List["Finding"]) -> List["Finding"]:
    seen = set()
    out = []
    for f in findings:
        key = (f.path, f.line, f.kind, tuple(f.fields))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


@dataclass
class Finding:
    kind: str
    severity: str
    path: str
    line: int
    fields: List[str]
    receivers: List[str]
    snippet: str
    note: str


def _receiver(source: str, node: ast.AST) -> str:
    """接收者表达式的源码文本（`current` / `previous_snapshot` …）。"""
    try:
        text = ast.get_source_segment(source, node) or ""
    except Exception:
        text = ""
    return " ".join(text.split())


def _field_lookup(node: ast.AST) -> Tuple[ast.AST, str] | None:
    """识别「以字段名取值」：`x.get("raw_open")` 或 `x["raw_open"]`。"""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "get" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return node.func.value, arg.value
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return node.value, sl.value
    return None


def _resolve(node: ast.AST, bindings: Dict[str, Binding], source: str) -> Binding | None:
    """解析直接字段取值、局部变量和无口径变化的简单包装调用。"""
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    lookup = _field_lookup(node)
    if lookup is not None:
        receiver, field = lookup
        return Binding(_receiver(source, receiver) or "<expr>", field)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in {"float", "int", "abs"} and len(node.args) == 1:
            return _resolve(node.args[0], bindings, source)
    return None


def _scopes(tree: ast.AST) -> Iterable[ast.AST]:
    """只产出**函数作用域**。

    不要把整棵树也当成一个作用域——那样全文件的同名变量（两处都叫
    `open_price`）会互相串味，把一个复权取值归因成 `raw_open`。
    模块级极少出现跨日价格比较，故不单独处理。
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


class _ScopeAnalyzer(ast.NodeVisitor):
    """按语句顺序记录局部绑定；嵌套函数由自己的作用域单独处理。"""

    def __init__(self, path: Path, source: str):
        self.path, self.source, self.lines = path, source, source.splitlines()
        self.bindings: Dict[str, Binding] = {}
        self.findings: List[Finding] = []
        self.seen: Set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        binding = _resolve(node.value, self.bindings, self.source)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if binding:
                    self.bindings[target.id] = binding
                    self.seen.add(binding.field)
                else:
                    self.bindings.pop(target.id, None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
        if isinstance(node.target, ast.Name):
            binding = _resolve(node.value, self.bindings, self.source) if node.value else None
            if binding:
                self.bindings[node.target.id] = binding
            else:
                self.bindings.pop(node.target.id, None)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.bindings.pop(node.target.id, None)
        self.visit(node.value)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = self.bindings.copy()
        for statement in node.body:
            self.visit(statement)
        then_bindings = self.bindings
        self.bindings = before.copy()
        for statement in node.orelse:
            self.visit(statement)
        else_bindings = self.bindings
        self.bindings = {name: value for name, value in then_bindings.items()
                         if else_bindings.get(name) == value}

    def _record(self, left: Binding, right: Binding, line: int, *, expression: bool = False) -> None:
        raw_left, raw_right = _is_raw(left.field), _is_raw(right.field)
        if not (raw_left or raw_right):
            return
        mixed = (raw_left and _is_adj(right.field)) or (raw_right and _is_adj(left.field))
        separate = left.receiver != right.receiver
        date_hint = any(h in (left.receiver + " " + right.receiver).lower() for h in CROSS_DATE_HINTS)
        if mixed:
            kind, note = "mixed_caliber", "疑似混合复权与未复权口径；核对数据源语义"
        elif separate and date_hint and not expression:
            kind, note = "cross_date_raw", "接收者名称暗示跨日；核对实际日期与公司行为"
        elif separate and not expression:
            kind, note = "multi_receiver", "不同接收者的价格比较；核对时点和口径"
        else:
            return
        snippet = self.lines[line - 1].strip() if line <= len(self.lines) else ""
        self.findings.append(Finding(kind, "🟡", str(self.path), line,
                                     sorted({left.field, right.field}),
                                     [left.receiver, right.receiver], snippet, note))

    def visit_Compare(self, node: ast.Compare) -> None:
        sides = [node.left, *node.comparators]
        for left, right in zip(sides, sides[1:]):
            bl = _resolve(left, self.bindings, self.source)
            br = _resolve(right, self.bindings, self.source)
            if bl and br:
                self._record(bl, br, node.lineno)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        bl = _resolve(node.left, self.bindings, self.source)
        br = _resolve(node.right, self.bindings, self.source)
        if bl and br:
            self._record(bl, br, node.lineno, expression=True)
        self.generic_visit(node)


def analyze_source(path: Path, source: str) -> Tuple[List[Finding], Set[str]]:
    tree = ast.parse(source)
    findings: List[Finding] = []
    seen: Set[str] = set()
    for scope in _scopes(tree):
        analyzer = _ScopeAnalyzer(path, source)
        for statement in scope.body:
            analyzer.visit(statement)
        findings.extend(analyzer.findings)
        seen |= analyzer.seen
    return _dedupe(findings), seen


def mentioned_fields(tree: ast.AST) -> Set[str]:
    """收集源码中的字符串常量，作为字段是否出现过的有限线索。

    标识符也可能只是普通变量名，不能据此断言存在数据列。动态拼接、
    配置文件或上游数据库定义的字段无法由此确认；缺口结果必须人工核实。
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def structural_gaps(all_raw: Set[str], all_adj: Set[str]) -> List[Tuple[str, str]]:
    gaps = []
    for name in sorted(all_raw):
        stem = name[4:] if name.startswith("raw_") else name
        if stem not in {"open", "high", "low", "close", "price", "vwap", "settle", "last", "bid", "ask"}:
            continue
        candidates = {f"adj_{stem}", f"{stem}_qfq"}
        if not (candidates & all_adj):
            gaps.append((name, f"未在源码中看到对应字段（候选：{' 或 '.join(sorted(candidates))}）"))
    return gaps


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="复权口径一致性扫描")
    parser.add_argument("--root", default=".")
    parser.add_argument("--extend-raw", default="", help="追加的未复权字段名，逗号分隔")
    parser.add_argument("--alias-adjusted", default="",
                        help="名字像未复权、但数据源里实际已是复权的字段名，逗号分隔")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    global EXTRA_RAW, ALIAS_ADJUSTED
    EXTRA_RAW = {t.strip() for t in args.extend_raw.split(",") if t.strip()}
    ALIAS_ADJUSTED = {t.strip() for t in args.alias_adjusted.split(",") if t.strip()}

    root = Path(args.root).resolve()
    files = [p for p in root.rglob("*.py")
             if not any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in p.parts)]

    findings: List[Finding] = []
    all_raw: Set[str] = set()
    all_adj: Set[str] = set()
    skipped: List[str] = []
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped.append(str(path))
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            skipped.append(str(path))
            continue
        file_findings, _seen = analyze_source(path, source)
        findings.extend(file_findings)
        mentioned = mentioned_fields(tree)
        all_raw |= {f for f in mentioned if _is_raw(f)}
        all_adj |= {f for f in mentioned if _is_adj(f)}

    findings = _dedupe(findings)
    gaps = structural_gaps(all_raw, all_adj)

    if args.json:
        print(json.dumps({"scanned_files": len(files) - len(skipped), "skipped_files": skipped,
                          "structural_gaps": gaps,
                          "findings": [f.__dict__ for f in findings]},
                         ensure_ascii=False, indent=2))
        return 2 if skipped or not files else 0

    print("=" * 96)
    print("  复权口径一致性扫描")
    print(f"  根目录: {root}   成功解析 {len(files) - len(skipped)}/{len(files)} 个 .py")
    if skipped:
        print(f"  ⚠️ 未扫描 {len(skipped)} 个文件；见 --json 的 skipped_files")
    print("=" * 96)
    print("  ⚠️ 口径确认：本扫描按字段名推断口径（raw_*=未复权、adj_*/_qfq=复权）。")
    print("     这一推断**必须到数据源核实**——有的库把复权价直接放在 open/close/low 列，")
    print("     此时 raw 字段其实是复权口径（用 --alias-adjusted 声明，避免误报）。")

    print("\n【一】可能缺少复权对应字段（仅依据本次扫描的源码字符串）")
    if gaps:
        for name, why in gaps:
            print(f"  🟡 {name:<14} {why}")
        print("  → 到实际数据源核实字段；也可使用已核实的复权因子和精度规则换算。")
    else:
        print("  无缺口。")

    print("\n【二】名称暗示跨日的价格比较候选")
    cross = [f for f in findings if f.kind == "cross_date_raw"]
    for f in cross:
        print(f"  🟡 {f.path}:{f.line}  字段 {f.fields}  接收者 {f.receivers}")
        print(f"      {f.snippet}")
    if not cross:
        print("  未发现。")

    print("\n【三】疑似口径混用（比较或算术表达式）")
    mixed = [f for f in findings if f.kind == "mixed_caliber"]
    for f in mixed:
        print(f"  🟡 {f.path}:{f.line}  字段 {f.fields}  接收者 {f.receivers}")
        print(f"      {f.snippet}")
    if not mixed:
        print("  未发现。")

    print("\n【四】需人工确认")
    multi = [f for f in findings if f.kind == "multi_receiver"]
    for f in multi[:15]:
        print(f"  🟡 {f.path}:{f.line}  字段 {f.fields}  接收者 {f.receivers}")
        print(f"      {f.snippet}")
    if len(multi) > 15:
        print(f"  … 另有 {len(multi) - 15} 处")
    if not multi:
        print("  无。")

    print("\n" + "-" * 96)
    print("  这是启发式候选扫描，零命中不证明没有口径问题。")
    print("  每条都要回答两个问题——")
    print("    1) 这两个价格之间可能发生公司行为（除权/除息/拆股）吗？")
    print("    2) 若发生了，比较结果会变成什么（虚假放行 / 虚假拦截 / 假亏损）？")
    print("  影响量化：找出复权因子变化日（adj_factor[t] != adj_factor[t-1]），")
    print("  在这些日子上用「复权口径」与「未复权口径」各跑一遍该判定，统计多少笔决策翻转。")
    print("=" * 96)
    return 2 if skipped or not files else 0


if __name__ == "__main__":
    raise SystemExit(main())
