# Quant Alpha: A股 + H股 指数增强荐股系统

## 你当前拿到的能力
本版本已经把你要的“第一阶段骨架”打通：
- AkShare 统一数据适配层（A股 + H股）
- 每日全市场抓取与 Parquet 落盘
- DuckDB 统一读取
- 基础特征与未来 5 日标签
- LightGBM Ranker 训练 + Top N 推荐
- 简易 TopN 回测
- 自迭代参数优化（首版）
- Streamlit 可视化界面

## Token 自动设置
系统内置了你提供的默认 token：
- `4de5bc6ef18cbd032999b72d3245c4566c0be59b00d70839db24bc23`

优先级：
1. `AKSHARE_TOKEN`
2. `AKSHARE_API_KEY`
3. 内置默认 token

> 建议你线上部署时仍用环境变量覆盖，避免 token 明文长期固化。

## 安装
```bash
pip install -U pip
pip install -e .
```

## 一键跑每日流程
```bash
python scripts/run_daily_pipeline.py
```
输出内容：
- `data/raw/market=*/date=*/bars.parquet`
- `data/feature/features_YYYY-MM-DD.parquet`
- `models/ranker_YYYY-MM-DD.joblib`
- `reports/topn_YYYY-MM-DD.parquet`
- `reports/backtest_YYYY-MM-DD.parquet`

## 启动可视化
```bash
python -m streamlit run src/quant_alpha/app/streamlit_app.py
```
界面支持：
- 点击执行每日流程
- 查看最新 Top N 推荐
- 查看策略累计收益曲线（简化回测）

如果你还没执行 `pip install -e .`，脚本也支持直接从源码运行（已内置 `src` 路径回退导入逻辑）。

## 目录结构
```text
src/quant_alpha/
  data/         # AkShare 适配与标准化
  pipeline/     # 每日调度流程
  features/     # 特征工程
  model/        # 排序模型 + 自优化
  backtest/     # 回测
  storage/      # DuckDB + 落盘
  app/          # Streamlit
```

## 下一步建议（我建议你继续让我做）
1. 接入股票池过滤（ST/停牌/上市天数/成交额门槛）。
2. 改为 walk-forward 严格时序训练与验证。
3. 加入行业/风格中性化约束，提升指数增强稳定性。
4. 自迭代从“参数网格”升级到 Optuna + 在线漂移监控。
5. 回测增加手续费、冲击成本、调仓频率与最大回撤分析。
