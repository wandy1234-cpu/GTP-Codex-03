# Prompt 04: Portfolio Optimizer

角色：组合经理 + 凸优化工程师。

实现指数增强优化器（cvxpy）：
- 目标：alpha - risk - turnover_cost
- 约束：TE、行业偏离、单票上限、换手、long-only
- 输出：目标权重 + 订单 JSON + 合规检查结果

市场规则：A/H 分支化处理（涨跌幅、board lot、交易时段）。
