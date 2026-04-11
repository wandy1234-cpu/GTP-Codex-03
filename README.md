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
- 股票池过滤（ST/停牌/流动性/上市天数可选）
- Walk-forward 严格时序训练与验证
- 分数中性化（行业/市值分桶可用时）
- Optuna 参数搜索 + 特征漂移监控

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
- 查看最新 Top N 推荐（含股票中文名称列）
- 查看策略累计收益曲线（简化回测）
- Top N 会按 `market+symbol` 去重，避免同一只股票重复出现在推荐列表中
- 模型会对全样本打分（而非仅测试子集），避免 TopN 只剩少量股票、回测点数过少

如果你还没执行 `pip install -e .`，脚本也支持直接从源码运行（已内置 `src` 路径回退导入逻辑）。

## 网络连接失败时的行为（已处理）
当 AkShare 临时网络异常（例如 `RemoteDisconnected`）时，系统会：
1. 对 spot/hist 接口自动重试（指数退避）。
2. 若 spot 仍失败，自动回退到该市场最近一次缓存的 `bars.parquet`（如果存在）。
3. 若无缓存，则尝试“核心种子股票历史数据回退”；再失败时启用“合成数据回退”（用于不中断调试流程）。
4. 在 Streamlit 页面展示“抓取告警”，并标记 `status=ok_with_warnings`。
   - 若仅触发“fallback to cache”，写入 `ingest.notes`（非 `ingest.errors`），不视为硬失败。
5. 缓存窗口回退会自动按 `market+symbol+date` 去重，避免 TopN 出现同一股票重复多行。
6. 若最新交易日可用股票数小于 `Top N`，系统会自动从更早日期补齐推荐，并在 `warnings` 中提示。

> 首次运行且没有任何缓存时，如果网络持续失败，系统会返回 `status=failed`，这是预期保护行为。
> Windows 下路径已统一按 POSIX pattern 处理，避免 `read_parquet` 通配符失效导致的 “No files found that match the pattern”。
> 若触发合成数据回退，结果仅用于流程连通性验证，不可直接用于实盘决策。
> 若模型阶段出现“Input data must be 2 dimensional and non empty”，系统现已改为返回结构化失败原因（`model stage failed: ...`），不会直接崩溃到页面。
> 若样本交易日不足，Ranker 会自动切换为“单交易日按股票维度切分 train/test”策略，尽量继续训练并减少无谓告警。

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
