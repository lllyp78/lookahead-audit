#!/usr/bin/env python3
"""回测单节点注入对照框架。

runner 提供 run() 和 injection_points()；框架执行基线、注入、恢复和复跑核对。
收益方向与幅度只描述观测结果，不单独证明注入有效或策略存在泄漏。
运行前参阅 references/injection_guide.md；--selftest 只验证本框架。
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import math
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class InjectionSpec:
    """一个注入点。

    patch / restore 成对出现；即使 patch 只完成一部分就抛错，restore 也必须安全。
    推荐在隔离副本里执行，或保存本次修改前的原始内容后精确恢复。
    """

    name: str
    patch: Callable[[], None]
    restore: Callable[[], None]
    category: str = "-"          # 对应 anti_patterns.md 的编号，如 A1/C4
    note: str = ""               # 一句话说明注入了什么


@dataclass
class InjectionResult:
    spec: InjectionSpec
    baseline: Dict[str, float]
    injected: Dict[str, float]
    metric: str
    diagnostics: Tuple[str, ...] = ()
    seconds: float = 0.0

    @property
    def delta_pct(self) -> float:
        base = self.baseline.get(self.metric, float("nan"))
        inj = self.injected.get(self.metric, float("nan"))
        if base is None or inj is None or not math.isfinite(base) or not math.isfinite(inj) or base == 0:
            return float("nan")
        return (inj - base) / abs(base) * 100.0

    @property
    def difference(self) -> float:
        return self.injected[self.metric] - self.baseline[self.metric]

    def diagnostic_changes(self, keys: Sequence[str]) -> List[Tuple[str, float, float, float]]:
        """返回 (指标, 基线, 注入后, 变化率) ；变化率用绝对值算，便于看数量级。"""
        out = []
        for key in keys:
            base = self.baseline.get(key)
            inj = self.injected.get(key)
            if base is None or inj is None or not math.isfinite(base) or not math.isfinite(inj):
                continue
            change = (inj - base) / abs(base) * 100.0 if base not in (0, None) else float("inf")
            out.append((key, base, inj, change))
        return out


# --------------------------------------------------------------------------
# 执行器
# --------------------------------------------------------------------------

# 活动量塌陷提示核查实验是否误伤了其他路径，不直接给泄漏结论。
DIAGNOSTIC_COLLAPSE_RATIO = 0.2   # 降到基线的 20% 以下即告警


class InjectionRunner:
    """基线 / 注入 / 恢复 / 对比。"""

    def __init__(
        self,
        run_fn: Callable[[], Dict[str, float]],
        *,
        metric: str = "total_return",
        diagnostics: Sequence[str] = (),
        quiet: bool = True,
    ) -> None:
        self.run_fn = run_fn
        self.metric = metric
        self.diagnostics = tuple(diagnostics)
        self.quiet = quiet

    def _run(self) -> Dict[str, float]:
        if not self.quiet:
            metrics = dict(self.run_fn())
        else:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                metrics = dict(self.run_fn())
        for key in (self.metric, *self.diagnostics):
            value = metrics.get(key)
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
                raise ValueError(f"回测指标缺失或非有限数值：{key}")
            metrics[key] = float(value)
        return metrics

    def baseline(self) -> Dict[str, float]:
        return self._run()

    def inject(self, spec: InjectionSpec) -> InjectionResult:
        started = time.time()
        before = self._run()
        try:
            spec.patch()
            after = self._run()
        finally:
            spec.restore()
        restored = self._run()
        if restored != before:
            raise RuntimeError(f"{spec.name} 恢复后基线不一致；停止后续试验")
        return InjectionResult(
            spec=spec, baseline=before, injected=after,
            metric=self.metric, diagnostics=self.diagnostics,
            seconds=time.time() - started,
        )


# --------------------------------------------------------------------------
# 判读与报告
# --------------------------------------------------------------------------

def collapsed_diagnostics(result: InjectionResult) -> List[Tuple[str, float, float]]:
    """活动量塌陷的诊断项：(指标, 基线, 注入后)。"""
    out = []
    for key, base, inj, _change in result.diagnostic_changes(result.diagnostics):
        if base and base > 0 and inj <= base * DIAGNOSTIC_COLLAPSE_RATIO:
            out.append((key, base, inj))
    return out


def verdict(result: InjectionResult) -> str:
    """解释观测结果；收益增减本身不能证明注入有效或无泄漏。"""
    collapsed = collapsed_diagnostics(result)
    if collapsed:
        names = ", ".join(f"{k} {b:.0f}→{i:.0f}" for k, b, i in collapsed)
        return f"⚠️ 活动量塌陷（{names}）：先核查注入影响了哪些路径"
    if not result.diagnostics:
        return "缺少活动量诊断，证据不足"
    if result.difference == 0:
        return "所选指标未变；仍需检查信号与交易轨迹"
    return "指标有变化；核对输入、信号与交易轨迹后解释原因"


def report(results: List[InjectionResult], diagnostics: Sequence[str]) -> None:
    width = max((len(r.spec.name) for r in results), default=10)
    width = max(width, 12)
    print()
    print("=" * 100)
    print("  注入对照报告（按所选指标的绝对差排序）")
    print("=" * 100)
    header = f"  {'注入点':<{width}} {'类别':<6} {'基线':>10} {'注入后':>10} {'差值':>10} {'相对变化%':>11}  "
    if diagnostics:
        header += " ".join(f"{d:>14}" for d in diagnostics) + "  "
    print(header + "判定")
    print("-" * 100)
    for r in sorted(results, key=lambda x: abs(x.difference), reverse=True):
        relative = f"{r.delta_pct:+.1f}" if math.isfinite(r.delta_pct) else "n/a"
        line = (f"  {r.spec.name:<{width}} {r.spec.category:<6} "
                f"{r.baseline.get(r.metric, float('nan')):>+10.2f} "
                f"{r.injected.get(r.metric, float('nan')):>+10.2f} "
                f"{r.difference:>+10.2f} {relative:>11}  ")
        for key, base, inj, _change in r.diagnostic_changes(diagnostics):
            line += f"{base:>7.0f}→{inj:<6.0f}"
        print(line + verdict(r))
    print("=" * 100)
    print("  差值只描述所选指标变化；请核查注入实际改变的输入、信号与交易路径。")
    print("  相对变化以 |基线| 为分母；基线为零时显示 n/a。不同指标不使用统一阈值。")
    print()


# --------------------------------------------------------------------------
# 自检：用内置假回测演示全流程（不需要任何真实项目）
# --------------------------------------------------------------------------

def _selftest() -> int:
    """合成回测：断言恢复、相同指标及活动量塌陷的处理。"""

    state = {"leak": 0, "killed": False, "collapse": False}

    def fake_run() -> Dict[str, float]:
        if state["killed"]:
            # 模拟"注入把某条路径掐死了"：成交为 0、收益塌陷 → Δ 为负
            return {"total_return": 5.0, "executed_trades": 0}
        if state["collapse"]:
            # 活动量塌陷但收益反而更高：Δ>0 也必须先排除注入设计
            return {"total_return": 30.0, "executed_trades": 8}
        return {
            "total_return": 20.0 + 10.0 * state["leak"],
            "executed_trades": 100 + 25 * state["leak"],
        }

    def apply(leak: int = 0, killed: bool = False, collapse: bool = False) -> Callable[[], None]:
        def _apply() -> None:
            state["leak"], state["killed"], state["collapse"] = leak, killed, collapse
        return _apply

    def restore() -> None:
        state["leak"], state["killed"], state["collapse"] = 0, False, False

    specs = [
        InjectionSpec("选股含当日", apply(1), restore, "A1",
                      "正常泄漏：Δ 正、诊断同步上升"),
        InjectionSpec("止损含当日", apply(2), restore, "A4", "剂量更大"),
        InjectionSpec("不生效的边界", apply(0), restore, "-",
                      "所选指标不变，仍须检查信号与交易"),
        InjectionSpec("误伤路径", apply(killed=True), restore, "-",
                      "让成交笔数掉到 0，须核查注入路径"),
        InjectionSpec("活动量塌陷但 Δ 为正", apply(collapse=True), restore, "-",
                      "成交 100→8 而 Δ>0：即使 Δ 为正也要先排除注入设计"),
    ]
    runner = InjectionRunner(fake_run, metric="total_return", diagnostics=("executed_trades",))
    results = [runner.inject(s) for s in specs]
    report(results, ("executed_trades",))
    assert all(r.baseline == fake_run() for r in results)
    assert "活动量塌陷" in verdict(results[-2])
    assert "活动量塌陷" in verdict(results[-1])
    assert "仍需检查" in verdict(results[2])
    print("  自检断言通过：恢复、零变化和活动量塌陷均按预期处理。")
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def _load_runner(path: str):
    spec = importlib.util.spec_from_file_location("injection_runner_module", Path(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load runner module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="未来函数注入测试（可适配）")
    parser.add_argument("--runner", help="提供 run() 与 injection_points() 的模块路径")
    parser.add_argument("--metric", default="total_return", help="用于计算 Δ 的指标键")
    parser.add_argument("--diagnostics", default="",
                        help="逗号分隔的诊断指标键，如 executed_trades,position_count")
    parser.add_argument("--selftest", action="store_true", help="用内置假回测演示全流程")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.runner:
        parser.error("需要 --runner（或先用 --selftest 自检）")

    module = _load_runner(args.runner)
    for required in ("run", "injection_points"):
        if not hasattr(module, required):
            raise AttributeError(f"runner module must define {required}()")

    diagnostics = tuple(k.strip() for k in args.diagnostics.split(",") if k.strip())
    runner = InjectionRunner(module.run, metric=args.metric, diagnostics=diagnostics)
    print(f"目标: {args.runner}  指标: {args.metric}  诊断: {diagnostics or '(未提供)'}")
    if not diagnostics:
        parser.error("请用 --diagnostics 指定至少一个活动量指标")
    specs: Iterable[InjectionSpec] = module.injection_points()
    specs = list(specs)
    if not specs:
        print("⚠️  injection_points() 返回空列表，没有完成验证。")
        return 2
    results = []
    for spec in specs:
        print(f"  注入 {spec.name} …", end="", flush=True)
        result = runner.inject(spec)
        results.append(result)
        print(f" 差值={result.difference:+.2f}  ({result.seconds:.0f}s)", flush=True)
    if results:
        report(results, diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
