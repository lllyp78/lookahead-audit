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
