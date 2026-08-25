"""优秀文献展示页面 — 筛选高分评价记录，对外可分享链接。

访问地址: http://<host>:6006/优秀文献展馆
"""

import sys, os
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from backend.services.history_service import load_history, get_record
from backend.services.llm_client import load_model
from backend.models.schemas import HistoryRecord


# ── Styling ─────────────────────────────────────────────────────
st.markdown("""
<style>
.exhibit-header {
    background: linear-gradient(135deg, #b8860b 0%, #8b6914 100%);
    padding: 2rem; border-radius: 10px; color: white; margin-bottom: 2rem;
}
.exhibit-card {
    background: #fafaf5; border-left: 6px solid #b8860b;
    padding: 1.2rem; margin: 1rem 0; border-radius: 8px;
    transition: box-shadow 0.2s;
}
.exhibit-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
.star-badge {
    display: inline-block; background: #b8860b; color: white;
    padding: 0.15rem 0.6rem; border-radius: 20px; font-size: 0.8rem;
    margin-right: 0.4rem;
}
</style>
""", unsafe_allow_html=True)


# ── Helpers (inlined to avoid importing from streamlit_app) ─────
def make_radar_chart(primary_results: list) -> go.Figure:
    values, dims = [], []
    for p in primary_results:
        s = p["score"] if isinstance(p, dict) else p.score
        w = p["weight"] if isinstance(p, dict) else p.weight
        pid = p["id"] if isinstance(p, dict) else p.id
        pname = p["name"] if isinstance(p, dict) else p.name
        pct = (s / w * 100) if w > 0 else 0
        values.append(round(pct, 1))
        dims.append(f"{pid} {pname}")
    if not values:
        return go.Figure()
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]], theta=dims + [dims[0]],
        fill="toself", line=dict(color="#b8860b", width=2),
        fillcolor="rgba(184,134,11,0.2)",
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(range=[0, 100], tickvals=[20, 40, 60, 80, 100])),
        height=300, margin=dict(l=30, r=30, t=30, b=20),
    )
    return fig


def make_gauge(score: float) -> go.Figure:
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"font": {"size": 36, "color": "#8b6914"}, "suffix": " 分"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#b8860b"},
            "steps": [
                {"range": [0, 60], "color": "#ffcdd2"},
                {"range": [60, 75], "color": "#fff9c4"},
                {"range": [75, 85], "color": "#c8e6c9"},
                {"range": [85, 100], "color": "#a5d6a7"},
            ],
        },
    ))
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=40, b=10))
    return fig


def score_to_stars(score: float) -> str:
    """Convert score to star rating string."""
    if score >= 95:
        return "⭐⭐⭐⭐⭐"
    elif score >= 88:
        return "⭐⭐⭐⭐"
    elif score >= 80:
        return "⭐⭐⭐"
    return "⭐⭐"


def score_grade(score: float) -> str:
    if score >= 95:
        return "卓越"
    elif score >= 88:
        return "优秀"
    elif score >= 80:
        return "良好"
    return "一般"


# ── Page ───────────────────────────────────────────────────────
st.markdown("""
<div class="exhibit-header">
    <h1>🏆 优秀文献展馆</h1>
    <p style="font-size:1.1rem;margin:0;opacity:0.9;">
        中医药政策研究评价系统 · 高分文献精选 · 可对外分享
    </p>
</div>
""", unsafe_allow_html=True)

# Warm the cache so history loads fast
load_model()

# ── Filter & load ──────────────────────────────────────────────
col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
with col_f1:
    threshold = st.slider(
        "最低分数阈值",
        min_value=60, max_value=100, value=80, step=5,
        help="只展示总分不低于此值的文献",
    )
with col_f2:
    sort_by = st.selectbox(
        "排序方式", ["总分从高到低", "总分从低到高", "最近评价"],
    )
with col_f3:
    st.caption("")  # spacer
    st.caption("")
    show_detail = st.checkbox("展开详情", value=True)

# Load all records
all_history = load_history(username=None)  # all users' records

# Filter & sort
filtered = [h for h in all_history if h.get("total_score", 0) >= threshold]

if sort_by == "总分从高到低":
    filtered.sort(key=lambda h: h.get("total_score", 0), reverse=True)
elif sort_by == "总分从低到高":
    filtered.sort(key=lambda h: h.get("total_score", 0))
elif sort_by == "最近评价":
    filtered.sort(key=lambda h: h.get("timestamp", ""), reverse=True)

# ── Stats bar ──────────────────────────────────────────────────
col_s1, col_s2, col_s3, col_s4 = st.columns(4)
with col_s1:
    st.metric("展馆收录", len(filtered))
with col_s2:
    avg = sum(h.get("total_score", 0) for h in filtered) / len(filtered) if filtered else 0
    st.metric("平均得分", f"{avg:.1f}")
with col_s3:
    top = max((h.get("total_score", 0) for h in filtered), default=0)
    st.metric("最高得分", f"{top:.1f}")
with col_s4:
    excellent_count = sum(1 for h in filtered if h.get("total_score", 0) >= 88)
    st.metric("卓越/优秀", excellent_count)

if not filtered:
    st.info(f"暂无评分 ≥ {threshold} 的文献。可降低阈值或等待更多评价后查看。")
    st.stop()

st.markdown("---")

# ── Score distribution ─────────────────────────────────────────
if len(filtered) >= 2:
    scores = [h.get("total_score", 0) for h in filtered]
    dist_fig = px.histogram(
        x=scores, nbins=min(10, len(filtered)),
        title="展馆文献评分分布",
        color_discrete_sequence=["#b8860b"],
        labels={"x": "总分", "y": "篇数"},
    )
    dist_fig.update_layout(height=200, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(dist_fig, use_container_width=True)

# ── Showcase cards ─────────────────────────────────────────────
st.markdown("## 📜 收录文献")

for h in filtered:
    hid = h.get("id", "")
    doc_name = h.get("doc_name", "未命名")
    total_score = h.get("total_score", 0)
    base_score = h.get("base_score", 0)
    timestamp = h.get("timestamp", "")
    overall = h.get("overall_comment", "")
    excluded = h.get("excluded_indicators", [])
    primary_results = h.get("primary_results", [])
    additional_results = h.get("additional_results", [])

    grade = score_grade(total_score)
    stars = score_to_stars(total_score)
    bonus_total = sum(a["score"] if isinstance(a, dict) else a.score for a in additional_results) if additional_results else 0

    with st.container():
        # Card header
        st.markdown(f"""
        <div class="exhibit-card">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <span style="font-size:1.3rem;font-weight:bold;color:#1a1a1a;">📄 {doc_name}</span>
                    <span class="star-badge">{grade}</span>
                    <span style="color:#888;font-size:0.85rem;">{timestamp}</span>
                </div>
                <div style="text-align:right;">
                    <span style="font-size:2rem;font-weight:bold;color:#b8860b;">{total_score}</span>
                    <span style="color:#666;">/ 100分  {stars}</span>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if show_detail:
            col_card1, col_card2 = st.columns([1, 2])

            with col_card1:
                st.plotly_chart(make_gauge(total_score), use_container_width=True)

            with col_card2:
                if primary_results:
                    # Convert dict results to proper objects if needed
                    try:
                        rad_results = []
                        for p in primary_results:
                            if isinstance(p, dict):
                                from backend.models.schemas import PrimaryResult, SecondaryResult
                                subs = []
                                for s in p.get("secondary_results", []):
                                    subs.append(SecondaryResult(
                                        id=s.get("id", ""), name=s.get("name", ""),
                                        max_score=s.get("max_score", 0), score=s.get("score", 0),
                                        evidence=s.get("evidence", ""), comment=s.get("comment", ""),
                                    ))
                                rad_results.append(PrimaryResult(
                                    id=p.get("id", ""), name=p.get("name", ""),
                                    weight=p.get("weight", 0), score=p.get("score", 0),
                                    secondary_results=subs,
                                ))
                            else:
                                rad_results.append(p)
                        st.plotly_chart(make_radar_chart(rad_results), use_container_width=True)
                    except Exception:
                        pass

            # Metrics summary
            score_cols = st.columns(min(len(primary_results), 7))
            for i, p in enumerate(primary_results):
                if isinstance(p, dict):
                    pid, pname, pscore, pweight = p.get("id", ""), p.get("name", ""), p.get("score", 0), p.get("weight", 0)
                else:
                    pid, pname, pscore, pweight = p.id, p.name, p.score, p.weight
                pct = (pscore / pweight * 100) if pweight > 0 else 0
                with score_cols[i]:
                    st.metric(f"{pid} {pname}", f"{pscore}/{pweight}", f"{pct:.0f}%")

            # Additional scores
            if additional_results:
                bonus_parts = " | ".join(
                    f"{a['name'] if isinstance(a, dict) else a.name}: {a['score'] if isinstance(a, dict) else a.score:+d}"
                    for a in additional_results
                )
                st.caption(f"附加分: {bonus_parts}")

            # Overall comment
            if overall:
                st.caption(f"💬 {overall[:200]}")

            # Direct link to full evaluation
            if hid:
                base_url = st.query_params.get("_", "")
                st.markdown(
                    f'<a href="/?history_id={hid}" target="_blank" '
                    f'style="display:inline-block;padding:0.3rem 0.8rem;'
                    f'background:#b8860b;color:white;border-radius:4px;'
                    f'text-decoration:none;font-size:0.85rem;">📋 查看完整评价报告</a>',
                    unsafe_allow_html=True,
                )

        st.markdown("---")
