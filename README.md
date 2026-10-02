# lookahead-audit

A reusable skill for auditing look-ahead bias and related data leakage in quantitative backtests. The audit method is market and language independent; the bundled static scanner supports Python only. Documentation inside the skill is in Chinese.

这是一个检查量化策略回测时序和数据泄漏的 skill。它追踪数据的**实际可用时间**、决策时间、订单与成交顺序，以及历史数据版本。附带脚本帮助做 Python 口径候选扫描、截断前缀对照和单节点注入；它们不能单独证明策略没有泄漏。

## 安装与使用

将仓库克隆或复制到 Codex skills 目录，使路径成为 `~/.codex/skills/lookahead-audit/SKILL.md`。在 Codex 中可以直接提出“审查这个回测是否有未来函数”，或显式使用 `$lookahead-audit`。仅阅读审计方法不需要 Python 依赖；辅助脚本要求 Python 3.9 或更新版本。

使用截断脚本前安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

先用内置合成数据看两种结果。下面第一条应返回 `0`（所测前缀一致），第二条应返回 `1`（早期信号依赖后续价格）；每次运行需使用新目录：

```bash
python3 scripts/truncation_consistency_test.py \
  --command "python3 examples/demo_backtest.py --start {start} --end {end} --output {output} --mode clean" \
  --output-template "/tmp/lookahead-demo-clean-{end}" \
  --start 2020-01-01 --ends 2020-01-03,2020-01-06 \
  --key-columns trade_date,code

python3 scripts/truncation_consistency_test.py \
  --command "python3 examples/demo_backtest.py --start {start} --end {end} --output {output} --mode leaky" \
  --output-template "/tmp/lookahead-demo-leaky-{end}" \
  --start 2020-01-01 --ends 2020-01-03,2020-01-06 \
  --key-columns trade_date,code
```

然后用自己的回测命令和**全新**输出路径运行，例如：

```bash
python3 scripts/truncation_consistency_test.py \
  --command "python3 -m your_project.backtest --start {start} --end {end} --output {output}" \
  --output-template "/tmp/lookahead-example-{end}" \
  --start 2020-01-01 \
  --ends 2022-12-30,2024-12-31 \
  --files daily_nav.csv
```

脚本遇到旧结果、空主要产物或无法验证的输入会返回非零。路径只是示意，回测入口和 CSV 日期列须按项目修改；同一模板第二次运行应换新的根目录。其他工具与适配说明见 [动态验证指南](references/injection_guide.md)。

## 范围与判断方式

- 核心规则：决策所用数据版本必须在决策时已可用；订单与成交顺序应可实现。T+1 开盘只是一个特定日频示例。
- 报告区分已确认泄漏、待核实候选、所测范围未发现和证据不足，并分别列出覆盖范围与实测影响。
- 动态对照只覆盖实际触发的路径；收益上升、下降或不变都不能单独给出最终判定。
- 价格复权口径、成交假设和研究过程选择偏差可以一并指出，但应和时间泄漏分别报告。

从 [SKILL.md](SKILL.md) 开始阅读，按需进入 [时间契约](references/temporal_contract.md)、[反模式参考](references/anti_patterns.md)、[中国股票与 ETF 示例](references/markets/cn-equities.md) 或 [机器学习策略检查](references/ml-leakage.md)。

## 开发

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。代码与文档按 [MIT License](LICENSE) 开源。
