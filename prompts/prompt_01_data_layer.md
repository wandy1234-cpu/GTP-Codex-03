# Prompt 01: Data Layer

角色：量化数据工程师+审计治理专家。

请基于 `config/config.example.yaml` 生成 A/H 数据层代码：
1. adapters 统一接口；
2. 字段映射到 required_fields；
3. company action 与 as-of join；
4. data quality 报告。

要求：
- 缺失字段直接 ERROR；
- 不可用 source 自动降级并记录 warning；
- 输出目录沿用当前工程 `src/quant_alpha/*`。
