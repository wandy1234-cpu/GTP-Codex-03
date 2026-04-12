"""Streamlit UI for Quant Alpha system."""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

try:
    from quant_alpha.pipeline.run_daily import run_daily
except ModuleNotFoundError:
    # 兼容未执行 `pip install -e .` 的本地直接运行场景
    src_root = Path(__file__).resolve().parents[2]
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from quant_alpha.pipeline.run_daily import run_daily

st.set_page_config(page_title="Quant Alpha", layout="wide")
st.title("Quant Alpha: A股/H股 指数增强系统")

st.sidebar.header("操作")
top_n = st.sidebar.number_input("Top N", min_value=5, max_value=50, value=10, step=1)
enable_optimization = st.sidebar.checkbox("启用优化搜索（更慢）", value=False)
run_btn = st.sidebar.button("执行每日流程")

if run_btn:
    try:
        progress_text = st.empty()
        progress = st.progress(0)

        def _on_progress(pct: float, msg: str) -> None:
            progress.progress(int(pct * 100))
            progress_text.info(f"{int(pct * 100)}% - {msg}")

        with st.spinner("正在拉取数据、训练模型、生成推荐..."):
            result = run_daily(top_n=int(top_n), progress_cb=_on_progress, enable_optimization=enable_optimization)
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
        st.json(result)
        cov = (result.get("ingest") or {}).get("coverage", {})
        if cov:
            st.subheader("数据覆盖率检查（A/H）")
            st.dataframe(pd.DataFrame(cov).T, use_container_width=True)
        ingest_errors = (result.get("ingest") or {}).get("errors", {})
        for market, msg in ingest_errors.items():
            if msg:
                st.warning(f"{market} 市场抓取告警: {msg}")
        ingest_notes = (result.get("ingest") or {}).get("notes", {})
        if ingest_notes:
            with st.expander("数据源回退提示（可忽略）", expanded=False):
                for market, msg in ingest_notes.items():
                    st.caption(f"{market}: {msg}")
        for msg in result.get("warnings", []):
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
