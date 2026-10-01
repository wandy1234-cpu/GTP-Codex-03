# Prompt 03: Model Training

角色：量化ML工程师。

基于现有 `lgbm_ranker + walk-forward`：
- 标签：excess_return_vs_benchmark
- 训练：rolling train/valid/test
- 超参：Optuna
- 输出 model_report + drift_baseline

必须做泄漏检查，发现泄漏直接 ERROR。
