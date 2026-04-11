# GTP-Codex-03

# Quant Alpha: A股 + H股 指数增强荐股系统

## 阶段一目标（进行中）
- 统一数据适配层（AkShare）
- 数据清洗、Parquet 落盘、DuckDB 查询
- 股票池过滤（停牌/ST/上市天数/流动性）
- 基础标签与特征工程
- LightGBM Ranker 主流程
- Walk-forward 回测基础框架
- Top10 推荐输出
- Streamlit 多页面 GUI 骨架

## 第一步交付：AkShare 数据接口

### 1) 环境安装
```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

### 2) Token 配置
```bash
export AKSHARE_TOKEN="你的token"
# 或者
export AKSHARE_API_KEY="你的token"
```

> 当前实现会读取 token 并保留配置入口，便于后续接入需要鉴权的接口。

### 3) 快速验证
```bash
python scripts/smoke_akshare.py
```

### 4) 当前接口能力
`AkshareAdapter`（`src/quant_alpha/data/akshare_adapter.py`）已提供：
- `fetch_spot("A")`：A股实时行情（标准化字段）
- `fetch_spot("HK")`：H股实时行情（标准化字段）
- `fetch_history(symbol, market, start, end, adjust, period)`：A/H 日线/周线/月线历史行情（标准化字段）

标准化后的关键字段包含：
- 通用标识：`symbol`, `market`, `name`
- 价格量能：`open`, `high`, `low`, `close`, `volume`, `amount`
- 常用衍生：`pct_change`, `change`, `turnover_rate`

## 后续建议（下一步）
1. 增加“交易日历 + 增量同步”模块。
2. 增加 Parquet 分区落盘（按 `market/date`）。
3. 增加 DuckDB 查询层与数据质量校验（缺失值、重复、停牌占比）。
