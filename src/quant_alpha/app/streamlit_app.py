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
run_btn = st.sidebar.button("执行每日流程")

if run_btn:
    try:
        with st.spinner("正在拉取数据、训练模型、生成推荐..."):
            result = run_daily(top_n=int(top_n))
        if result.get("status") == "ok":
            st.success("流程执行完成")
        else:
            st.warning("流程完成，但出现数据问题，请查看返回信息。")
        st.json(result)
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

st.subheader("Top N 推荐")
if topn_files:
    topn = pd.read_parquet(topn_files[-1])
    st.dataframe(topn, use_container_width=True)
else:
    st.info("暂无推荐结果，请先执行每日流程")

st.subheader("回测曲线")
if bt_files:
    bt = pd.read_parquet(bt_files[-1])
    bt["date"] = pd.to_datetime(bt["date"])
    st.line_chart(bt.set_index("date")["cum_ret"])
    st.dataframe(bt.tail(20), use_container_width=True)
else:
    st.info("暂无回测结果，请先执行每日流程")
