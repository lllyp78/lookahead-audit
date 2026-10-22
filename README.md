# lookahead-audit

A reusable skill for auditing look-ahead bias and related data leakage in quantitative backtests. The audit method is market and language independent; the bundled static scanner supports Python only. Documentation inside the skill is in Chinese.

这是一个检查量化策略回测时序和数据泄漏的 skill。它追踪数据的**实际可用时间**、决策时间、订单与成交顺序，以及历史数据版本。附带脚本帮助做 Python 口径候选扫描、截断前缀对照、单节点注入、决策时点重放和候选/权重重放；它们不能单独证明策略没有泄漏。

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

## 决策时点重放

```bash
python3 scripts/decision_time_replay.py \
  --full full_rank.csv --cutoff cutoff_rank.csv \
  --group-columns trade_date --key-columns trade_date,symbol \
  --rank-column score --top-k 2
```

比较“全量数据排序”与“仅 `selection_at` 前可用数据排序”的 top-k、top-1、共同键相关性和缺失/新增键。退出码与限制见[动态验证指南](references/injection_guide.md)。

可运行的最小例子（执行层窗口量进入排序）：

```bash
python3 examples/demo_execution_rank_leak.py --out /tmp/rank-leak --mode clean   # 返回 0
python3 examples/demo_execution_rank_leak.py --out /tmp/rank-leak --mode leaky   # 返回 1
```

## 候选 / 退出路径 membership 与权重

```bash
python3 scripts/membership_time_replay.py \
  --full full_membership.csv --cutoff cutoff_membership.csv \
  --group-columns entry_date --key-columns symbol \
  --membership-column included --pnl-column pnl
```

比较候选/退出路径的入选集合、权重变化和可选 PnL；权重模式用 `--weight-column weight --weight-threshold 0`。退出码与限制见[动态验证指南](references/injection_guide.md)。

可运行的最小例子（候选过滤用 T+1 开盘价）：

```bash
python3 examples/demo_membership_filter_leak.py --out /tmp/membership-leak --mode clean   # 返回 0
python3 examples/demo_membership_filter_leak.py --out /tmp/membership-leak --mode leaky   # 返回 1
```

## 范围与判断方式

- 核心规则：决策所用数据版本必须在决策时已可用；订单与成交顺序应可实现。T+1 开盘只是一个特定日频示例。决策要拆成 `selection_at`（选什么、排序、仓位）、`order_at` 和 `fill_at`。
- 候选生成、过滤、`dropna`、`weight=0` 也是决策；一个信号文件的行集合本身就是一次选择，不能只审引擎读到的列。
- A 股竞价（示例）：开盘价 9:25 才确定；要在开盘价成交，竞价买单必须在此前提交；限价单只能表达一侧，无法同时表达“低开在某个区间内”。
- 执行模型可以用未来量估算容量、滑点或成交价，但 fill amount、capacity、成交价、窗口量等输出不得回流到选择、排序、过滤或仓位。
- 预计算输入（CSV/parquet/特征文件）里进入选择、排序、仓位或订单的列，必须追到生产脚本；未追溯是 blocking evidence gap，不能判“未发现”。排序键也算决策输入。
- 报告区分已确认泄漏、待核实候选、所测范围未发现和证据不足，并分别列出覆盖范围与实测影响。
- 动态对照只覆盖实际触发的路径；收益上升、下降或不变都不能单独给出最终判定。
- 价格复权口径、成交假设和研究过程选择偏差可以一并指出，但应和时间泄漏分别报告。

从 [SKILL.md](SKILL.md) 开始阅读，按需进入 [时间契约](references/temporal_contract.md)、[反模式参考](references/anti_patterns.md)、[中国股票与 ETF 示例](references/markets/cn-equities.md) 或 [机器学习策略检查](references/ml-leakage.md)。写报告时使用 [报告模板](references/report_template.md)，其中包含决策输入来源表、选择/执行分离表和动态验证记录。

## 开发

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。代码与文档按 [MIT License](LICENSE) 开源。
