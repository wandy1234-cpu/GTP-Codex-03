# Prompt 02: Factor Library

角色：多因子研究员。

根据 config 的 factors.pool 生成因子函数库和评估脚本：
- preprocess: winsorize -> normalize -> neutralize
- 报告输出 Rank-IC、分组收益、衰减分析

约束：
- 财务数据按披露时点 as-of 对齐
- 缺失行业分类时降级到市值中性化并警告
