# 动态验证指南

动态测试用于补充源码与数据溯源。先确认回测可重复：相同代码、数据版本、初始状态、随机种子和配置应产生相同轨迹。验证前选好输入时间边界及可比较的信号、订单、持仓或净值文件。

## 截断前缀对照

`scripts/truncation_consistency_test.py` 从同一起点使用多个截止日运行回测，并比较短运行的历史前缀与长运行对应期间的输出。

```bash
python3 scripts/truncation_consistency_test.py \
  --command "python3 -m your_project.backtest --start {start} --end {end} --output {output}" \
  --output-template "/tmp/your-fresh-audit-{end}" \
  --start 2020-01-01 \
  --ends 2022-12-30,2024-12-31 \
  --files daily_nav.csv \
  --date-column trade_date \
  --key-columns trade_date
```

每次使用新的输出目录；已有内容会报错，防止复用旧运行。`--files` 支持 glob，并比较所有实际匹配文件；首个模式对应的文件须有记录。同一次调用的文件需共享 `--key-columns`，多资产净值通常指定 `trade_date,code`。不同结构的产物可分别运行。默认逐值比较字符串；只在明确知道易变列时一起指定 `--strip-columns` 和 `--strip-regex`。不要把代码、标的、成交标识等有审计意义的列设成易变列。

退出码：`0` 所测前缀一致；`1` 发现差异；`2` 输入、运行或覆盖不足，无法判定。差异先排除正常的期末强平、配置/数据版本变化、缓存及随机性；一致也只覆盖实际比较到的路径。脚本对日期按 UTC 解析，跨时区产物要先统一日期含义。

## 决策时点重放

`scripts/decision_time_replay.py` 比较两套决策产物：一套用全量数据，另一套只用 `selection_at` 前可用的数据。按组比较 top-k 集合、top-1 是否改变、共同键的排序相关性。

```bash
python3 scripts/decision_time_replay.py \
  --full full_rank.csv --cutoff cutoff_rank.csv \
  --group-columns trade_date --key-columns trade_date,symbol \
  --rank-column score --top-k 2 --out replay.json
```

退出码：`0` 所测组 top-k 完全一致且没有缺失/新增键；`1` 有差异；`2` 输入无效。**一致只说明所测组、所测 top-k 在两种输入下相同，不证明没有泄漏；差异也要先排除 cutoff 实现、数据版本、缺失行和并列处理。** 截断前缀测试按回测终点截断，抓不到每行只向后看一小段（例如较早决策用了较晚窗口量）的泄漏；两种测试互补。

## 成员 / 权重重放

`scripts/membership_time_replay.py` 比较全量与 `selection_at` 截断口径的候选/退出路径 membership 或 weight：按组报告新增/剔除/调权，以及可选 PnL。

```bash
python3 scripts/membership_time_replay.py \
  --full full_membership.csv --cutoff cutoff_membership.csv \
  --group-columns entry_date --key-columns symbol \
  --membership-column included --pnl-column pnl --out replay.json
```

权重模式用 `--weight-column weight --weight-threshold 0`。一端入选、另一端未入选算新增/剔除，不算调权；`membership=0` 与缺失都表示未入选。

退出码：`0` 无差异；`1` 有差异；`2` 输入无效。**一致不证明无泄漏；差异先排除 cutoff 实现、数据版本、缺失行和并列处理。** 可运行例子见 `examples/demo_membership_filter_leak.py`（候选过滤用 T+1 开盘价）。

## 单节点注入对照

当且仅当能够隔离一个输入边界时，在可恢复的副本里故意让该边界提前读取一个原本尚不可用的版本，再运行相同回测。通用接口：

```bash
python3 scripts/inject_test.py --runner path/to/runner.py \
  --metric total_return --diagnostics executed_trades,position_count
```

runner 提供 `run() -> dict[str, float]` 和 `injection_points() -> list[InjectionSpec]`。参考 `scripts/injection_runner_example.py`，将命令、指标解析和注入点适配到项目。框架会在注入前后运行基线，异常时调用恢复函数；恢复后重新运行并核对指标。注入函数即使执行到一半抛错，恢复函数也应安全。不要使用 `git checkout` 清除用户未提交的文件内容。

示例适配器通过原子替换普通文件做文本注入；它会拒绝符号链接和多重硬链接，以免改变链接结构。请在隔离副本中运行，并确保没有其他进程同时写入目标文件。

每个试验至少记录：

1. 原输入与注入后输入确实不同，其他数据路径是否保持一致。
2. 对应的信号、订单、交易、持仓和所选指标变化。
3. 活动量是否异常塌陷；如果塌陷，查清是否误伤了执行路径。
4. 实验能够覆盖的日期、资产和分支；未触发的节点标为未验证。

所选指标相同不能证明边界没有影响；交易可以变化而总收益恰好相同。收益下降也可能是有效注入的真实结果。相对变化仅在基线不为零时展示，并以基线绝对值为分母；跨指标不使用统一阈值。没有诊断指标、没有注入点或恢复后基线不同的试验不应标为通过。

脚本返回值：正常完成为 `0`，空注入点为 `2`；回测、注入或恢复报错时进程返回非零。`--selftest` 提供内置假回测和基本断言，仅验证框架，不验证任何外部策略。
