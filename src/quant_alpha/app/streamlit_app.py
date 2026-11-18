"""Streamlit UI for Quant Alpha system."""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

try:
    from quant_alpha.backtest.yearly import run_one_year_backtest
    from quant_alpha.etf.pipeline import recommend_etfs
    from quant_alpha.market.broad_index import A_BROAD_INDEXES, HK_BROAD_INDEXES, fetch_broad_index_quotes, market_median_change
    from quant_alpha.pipeline.run_daily import run_daily
except ModuleNotFoundError:
    # 兼容未执行 `pip install -e .` 的本地直接运行场景
    src_root = Path(__file__).resolve().parents[2]
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from quant_alpha.backtest.yearly import run_one_year_backtest
    from quant_alpha.etf.pipeline import recommend_etfs
    from quant_alpha.market.broad_index import A_BROAD_INDEXES, HK_BROAD_INDEXES, fetch_broad_index_quotes, market_median_change
    from quant_alpha.pipeline.run_daily import run_daily

st.set_page_config(page_title="Quant Alpha", layout="wide")
st.title("Quant Alpha: A股/H股 指数增强系统")

st.sidebar.header("操作")
if "page" not in st.session_state:
    st.session_state["page"] = "推荐与回测"
if st.sidebar.button("推荐与回测", use_container_width=True):
    st.session_state["page"] = "推荐与回测"
if st.sidebar.button("宽基指数", use_container_width=True):
    st.session_state["page"] = "宽基指数"
if st.sidebar.button("ETF推荐", use_container_width=True):
    st.session_state["page"] = "ETF推荐"
    st.session_state["run_etf_now"] = True
if st.sidebar.button("一年历史回测", use_container_width=True):
    st.session_state["page"] = "一年历史回测"
    st.session_state["run_year_backtest_now"] = True
if st.sidebar.button("执行每日流程", use_container_width=True):
    st.session_state["page"] = "推荐与回测"
    st.session_state["run_daily_now"] = True
page = st.session_state["page"]


@st.cache_data(ttl=180)
def _cached_broad_index_quotes() -> pd.DataFrame:
    return fetch_broad_index_quotes()


@st.cache_data(ttl=60)
def _cached_market_median_change(root: str) -> pd.DataFrame:
    return market_median_change(Path(root) / "data" / "raw")


if page == "宽基指数":
    st.subheader("主要宽基指数")
    if st.button("刷新本地市场中位数"):
        _cached_market_median_change.clear()

    refresh_live_index = st.button("刷新实时指数行情")
    if refresh_live_index:
        _cached_broad_index_quotes.clear()
        idx = _cached_broad_index_quotes()
    else:
        idx = pd.DataFrame(
            [{"market": "A", "symbol": k, "name": v, "close": pd.NA, "pct_change": pd.NA, "source": "点击刷新实时指数行情"} for k, v in A_BROAD_INDEXES.items()]
            + [{"market": "HK", "symbol": k, "name": v, "close": pd.NA, "pct_change": pd.NA, "source": "点击刷新实时指数行情"} for k, v in HK_BROAD_INDEXES.items()]
        )
    med = _cached_market_median_change(str(Path.cwd()))

    a_med = med[med["market"].astype(str).eq("A")]["median_pct_change"] if not med.empty else pd.Series(dtype=float)
    hk_med = med[med["market"].astype(str).eq("HK")]["median_pct_change"] if not med.empty else pd.Series(dtype=float)
    col1, col2, col3 = st.columns(3)
    col1.metric("A股股票中位数涨跌幅", f"{float(a_med.iloc[0]):.2f}%" if not a_med.empty else "N/A")
    col2.metric("港股股票中位数涨跌幅", f"{float(hk_med.iloc[0]):.2f}%" if not hk_med.empty else "N/A")
    col3.metric("样本来源", med["change_source"].iloc[0] if not med.empty else "N/A")

    if not idx.empty:
        st.dataframe(idx, use_container_width=True, hide_index=True)
        for item in idx.attrs.get("errors", []):
            st.warning(f"{item.get('market')}: 指数接口暂不可用，{item.get('error')}")
    else:
        st.info("暂未取到宽基指数行情。")

    st.subheader("市场股票中位数涨跌幅")
    if not med.empty:
        st.dataframe(med, use_container_width=True, hide_index=True)
        if "close/open_proxy" in set(med["change_source"].astype(str)):
            st.caption("当前原始股票快照没有 pre_close 或 pct_change 字段，股票中位数使用 close/open 作为当日代理涨跌幅。")
    else:
        st.info("暂无本地股票快照，先执行一次日常流程后即可计算市场股票中位数。")
    st.stop()

if page == "ETF推荐":
    st.subheader("未来一周 ETF Top N")
    etf_top_n = st.sidebar.number_input("ETF Top N", min_value=1, max_value=30, value=5, step=1)
    refresh_etf = st.sidebar.checkbox("刷新全量ETF数据（较慢）", value=False)
    run_etf = bool(st.session_state.pop("run_etf_now", False))
    if st.button("重新生成ETF推荐", use_container_width=True):
        run_etf = True

    if run_etf:
        with st.spinner("正在获取ETF数据并排序..."):
            result = recommend_etfs(Path.cwd(), top_n=int(etf_top_n), force_refresh=refresh_etf)
        if result.status == "ok":
            st.success("ETF推荐已生成")
        elif result.status == "ok_with_warnings":
            st.warning("ETF推荐已生成，但部分ETF历史数据获取失败，已使用可用样本排序。")
        else:
            st.error("ETF推荐失败，请检查网络或缓存。")
        c1, c2, c3 = st.columns(3)
        c1.metric("ETF数量", result.etf_count)
        c2.metric("历史行数", result.rows)
        c3.metric("状态", result.status)
        if result.warnings:
            with st.expander("ETF数据提示", expanded=False):
                st.caption(f"共 {len(result.warnings)} 条提示。下面只显示简短类型，完整信息已写入报告 JSON。")
                short = []
                for msg in result.warnings[:20]:
                    text = str(msg)
                    short.append(text.split(":", 1)[0] if ":" in text else text)
                st.write(short)

    etf_files = sorted((Path.cwd() / "reports").glob("etf_topn_*.parquet"))
    if etf_files:
        etf_topn = pd.read_parquet(etf_files[-1])
        st.dataframe(etf_topn, use_container_width=True, hide_index=True)
    else:
        st.info("暂无ETF推荐结果，点击左侧“ETF推荐”或本页“重新生成ETF推荐”。")
    st.stop()

if page == "一年历史回测":
    st.subheader("过去一年模型回测：对标上证综指")
    bt_top_n = st.sidebar.number_input("回测 Top N", min_value=5, max_value=100, value=10, step=1)
    max_symbols = st.sidebar.number_input("最多下载股票数", min_value=50, max_value=3000, value=800, step=50)
    refresh_bt = st.sidebar.checkbox("刷新一年历史数据（较慢）", value=False)
    run_bt = bool(st.session_state.pop("run_year_backtest_now", False))
    if st.button("重新运行一年回测", use_container_width=True):
        run_bt = True
    if run_bt:
        with st.spinner("正在运行一年历史回测..."):
            result = run_one_year_backtest(
                Path.cwd(),
                top_n=int(bt_top_n),
                refresh=refresh_bt,
                max_symbols=int(max_symbols),
            )
        if result.status == "failed":
            st.error("一年回测失败：缺少过去一年历史行情。请检查网络后勾选刷新一年历史数据再运行。")
        elif result.status == "ok_with_warnings":
            st.warning("一年回测完成，但有数据告警。")
        else:
            st.success("一年回测完成")
        metrics = result.metrics or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("累计收益", f"{metrics.get('strategy_cum_ret', 0):.2%}" if metrics else "N/A")
        c2.metric("对上证超额", f"{metrics.get('cum_excess_vs_shanghai', 0):.2%}" if metrics else "N/A")
        c3.metric("最大回撤", f"{metrics.get('max_drawdown', 0):.2%}" if metrics else "N/A")
        c4.metric("胜率", f"{metrics.get('hit_rate', 0):.2%}" if metrics else "N/A")
        if result.warnings:
            with st.expander("回测数据提示", expanded=False):
                st.write([str(x).split(":", 1)[0] for x in result.warnings[:50]])

    bt_files = sorted((Path.cwd() / "reports").glob("one_year_backtest_*.parquet"))
    if bt_files:
        curve = pd.read_parquet(bt_files[-1])
        if not curve.empty and "date" in curve.columns:
            curve["date"] = pd.to_datetime(curve["date"], errors="coerce")
            chart_cols = [c for c in ["cum_ret", "cum_excess_ret"] if c in curve.columns]
            if chart_cols:
                st.line_chart(curve.set_index("date")[chart_cols])
            st.dataframe(curve.tail(30), use_container_width=True, hide_index=True)
        else:
            st.info("暂无可展示的一年回测曲线。")
    else:
        st.info("暂无一年回测结果。")
    st.stop()

top_n = st.sidebar.number_input("Top N", min_value=5, max_value=50, value=10, step=1)
fast_mode = st.sidebar.checkbox("快速模式（使用缓存，跳过实时抓取）", value=True)
enable_optimization = st.sidebar.checkbox("启用优化搜索（更慢）", value=False)
run_btn = bool(st.session_state.pop("run_daily_now", False))

if run_btn:
    try:
        progress_text = st.empty()
        progress = st.progress(0)

        def _on_progress(pct: float, msg: str) -> None:
            progress.progress(int(pct * 100))
            progress_text.info(f"{int(pct * 100)}% - {msg}")

        with st.spinner("正在拉取数据、训练模型、生成推荐..."):
            result = run_daily(
                top_n=int(top_n),
                progress_cb=_on_progress,
                enable_optimization=enable_optimization,
                smoke=fast_mode,
            )
        progress.progress(100)
        progress_text.success("100% - 流程完成")
        ingest = result.get("ingest") or {}
        errors = dict(ingest.get("errors") or {})
        notes = dict(ingest.get("notes") or {})
        # 兼容历史返回结构: 将 cache fallback 从 errors 自动降级到 notes
        for market, msg in list(errors.items()):
            if "fallback to cache" in str(msg):
                notes[market] = msg
                errors.pop(market, None)
        if ingest:
            result["ingest"] = {"rows": ingest.get("rows", {}), "errors": errors, "notes": notes}
        if result.get("status") == "ok":
            st.success("流程执行完成")
        else:
            st.warning("流程完成，但出现数据问题，请查看返回信息。")
        dq = result.get("data_quality") or {}
        qg = result.get("quality_gate") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("最新日期", dq.get("latest_date", "N/A"))
        c2.metric("原始覆盖股票数", dq.get("raw_latest_symbol_count", "N/A"))
        c3.metric("过滤后股票池", dq.get("filtered_latest_symbol_count", "N/A"))
        c4.metric("质量门禁", qg.get("status", "N/A"))
        by_market = pd.DataFrame(
            [
                {
                    "market": m,
                    "原始覆盖": (dq.get("raw_latest_by_market") or {}).get(m, 0),
                    "过滤后": (dq.get("filtered_latest_by_market") or {}).get(m, 0),
                }
                for m in sorted(set((dq.get("raw_latest_by_market") or {}) | (dq.get("filtered_latest_by_market") or {})))
            ]
        )
        if not by_market.empty:
            st.dataframe(by_market, use_container_width=True, hide_index=True)
        st.caption(f"推荐数量: {dq.get('actual_top_n_count', 'N/A')} / 请求 Top N: {dq.get('requested_top_n', 'N/A')}")
        with st.expander("查看完整流程返回信息", expanded=False):
            st.json(result)
        cov = (result.get("ingest") or {}).get("coverage", {})
        if cov:
            st.subheader("数据覆盖率检查（A/H）")
            st.dataframe(pd.DataFrame(cov).T, use_container_width=True)
        ingest_errors = (result.get("ingest") or {}).get("errors", {})
        for market, msg in ingest_errors.items():
            if msg:
                st.warning(f"{market} 市场抓取告警: {msg}")
        hidden_warnings = {
            "model_fallback:feature dataframe is empty after dropna",
            "insufficient_fold_count",
        }
        for msg in result.get("warnings", []):
            if str(msg) in hidden_warnings:
                continue
            if str(msg).startswith("latest_cross_section_score_fallback:"):
                continue
            st.warning(msg)
    except Exception as exc:
        st.error(f"执行失败: {exc}")

report_dir = Path.cwd() / "reports"
topn_files = sorted(report_dir.glob("topn_*.parquet"))
bt_files = sorted(report_dir.glob("backtest_*.parquet"))
exp_registry = report_dir / "experiments" / "registry.jsonl"
gov_state_file = report_dir / "governance" / "champion_state.json"
gov_hist_file = report_dir / "governance" / "governance_history.jsonl"
drift_files = sorted(report_dir.glob("drift_*.json"))
review_files = sorted(report_dir.glob("review_*.parquet"))

st.subheader("Top N 推荐")
if topn_files:
    topn = pd.read_parquet(topn_files[-1])
    topn_show = topn.copy().reset_index(drop=True)
    if "rank_position" not in topn_show.columns:
        topn_show.insert(0, "rank_position", range(1, len(topn_show) + 1))
    st.dataframe(topn_show, use_container_width=True, hide_index=True)
else:
    st.info("暂无推荐结果，请先执行每日流程")

st.subheader("回测曲线")
if bt_files:
    bt = pd.read_parquet(bt_files[-1])
    bt["date"] = pd.to_datetime(bt["date"])
    if len(bt) >= 2:
        st.line_chart(bt.set_index("date")["cum_ret"])
    else:
        st.info("回测数据点不足 2 个，暂无法绘制曲线。")
    st.dataframe(bt.tail(20), use_container_width=True)
else:
    st.info("暂无回测结果，请先执行每日流程")

st.subheader("系统健康卡 / Model Governance")
col1, col2, col3 = st.columns(3)
if gov_state_file.exists():
    import json

    state = json.loads(gov_state_file.read_text(encoding="utf-8"))
    champion = state.get("champion") or {}
    col1.metric("Champion", champion.get("model_version", "N/A"))
    col2.metric("Last Promotion", champion.get("promoted_at", "N/A"))
    col3.metric("Last Rollback", champion.get("rollback_at", "N/A"))
    with st.expander("Champion / Challenger 状态", expanded=False):
        st.json(state)
else:
    st.info("暂无 champion 状态，请先执行流程。")

if gov_hist_file.exists():
    import json

    rows = [json.loads(x) for x in gov_hist_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    if rows:
        st.subheader("Promotion / Rejection / Rollback 历史")
        st.dataframe(pd.DataFrame(rows).tail(50), use_container_width=True)

if drift_files:
    import json

    st.subheader("Drift Warnings")
    drift = json.loads(drift_files[-1].read_text(encoding="utf-8"))
    if drift.get("alerts"):
        for x in drift.get("alerts", []):
            st.warning(x)
    st.json(drift)

if exp_registry.exists():
    import json

    rows = [json.loads(x) for x in exp_registry.read_text(encoding="utf-8").splitlines() if x.strip()]
    if rows:
        st.subheader("Experiment Registry (latest)")
        st.dataframe(pd.DataFrame(rows).tail(30), use_container_width=True)

# Review history table intentionally hidden per operator feedback.
