# Contributing / 参与贡献

Issues and pull requests are welcome. Describe the decision time, data availability time, and a minimal reproducer for any proposed leakage rule. Include a legal counterexample when a pattern could occur in a valid strategy; avoid hard-coding rules from one market into the shared core.

请为新规则提供适用前提、最小复现和合法对照。脚本修复请补充行为测试，并使用合成数据；不要提交私有策略、数据库、密钥或交易记录。运行 `python -m unittest discover -s tests -v`，并在 PR 中说明实际验证结果及尚未覆盖的路径。

By contributing, you agree that your contributions are made available under the repository's MIT License. Preserve copyright and license notices when redistributing.
