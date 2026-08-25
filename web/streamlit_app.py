"""Streamlit frontend for the TCM Literature Evaluation System.

7 primary indicators x 16 secondary indicators, 100-point scale.
Supports single and batch (up to 5 files) document evaluation.
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from backend.services.evaluation_service import (
    evaluate_document, evaluate_secondary_indicator,
)
from backend.services.llm_client import load_model, clear_cache
from backend.services.history_service import (
    load_history, save_to_history, delete_record,
    get_ip_list, get_total_count, lookup_cached_result,
)
from backend.services.auth_service import (
    is_authenticated, logout, render_login_page, check_persisted_auth,
)
from backend.utils.document_parser import parse_document
from backend.utils.report_generator import (
    generate_report, get_mime_type, get_file_extension,
)
from shared.constants import (
    PRIMARY_INDICATORS, ALL_SECONDARY_INDICATORS,
    MAX_DOC_CHARS, PREVIEW_CHARS, OVERALL_THRESHOLDS,
)
import hashlib


# ── Cached helpers (avoid redundant work across reruns) ──────────
@st.cache_resource(show_spinner=False)
def _load_model_cached():
    """Cache the model in memory across all sessions. Only loaded once."""
    return load_model()


@st.cache_data(show_spinner=False, ttl=30)
def _load_history_cached(username: str):
    """Cache history list for 30s to avoid DB churn on every rerun."""
    if username:
        return load_history(username=username)
    return load_history()


@st.cache_data(show_spinner=False, ttl=30)
def _get_total_count_cached(username: str):
    """Cache total count for 30s."""
    if username:
        return get_total_count(ip_address=username)
    return 0


# ── Page config ──────────────────────────────────────────────────
st.set_page_config(
    page_title="中医药政策文献智能评价系统",
    page_icon="\U0001F4C4", layout="wide",
)

st.markdown("""
<style>
.tcm-header {
    background: linear-gradient(135deg, #2c7744 0%, #1a4d2c 100%);
    padding: 2rem; border-radius: 10px; color: white; margin-bottom: 2rem;
}
.score-card {
    background: #f8f9fa; border-left: 5px solid #2c7744;
    padding: 1rem; margin: 0.5rem 0; border-radius: 4px;
}
.primary-card {
    background: #f0f4f1; border-left: 6px solid #1a4d2c;
    padding: 1rem; margin: 1rem 0; border-radius: 6px;
}
.dimension-name { font-weight: bold; font-size: 1.05rem; color: #1a4d2c; }
.evidence-box {
    background: #fffef5; border: 1px solid #e0dcc0;
    padding: 0.75rem; margin: 0.3rem 0; border-radius: 4px; font-size: 0.9rem;
}
.batch-file-card {
    background: #f8f9fa; border: 1px solid #dee2e6;
    padding: 0.75rem 1rem; margin: 0.3rem 0; border-radius: 6px;
}
.compare-table { font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)


# ── Client IP detection ──────────────────────────────────────────
@st.cache_resource
def get_client_ip() -> str:
    """Detect a stable client identifier for history filtering.

    Tries X-Forwarded-For / X-Real-IP from reverse-proxy headers first,
    then falls back to the server's own IP. The result is cached so it
    stays stable across reruns within the same session.
    """
    import socket
    # Try reverse-proxy headers (available in newer Streamlit via _get_browser_address)
    try:
        from streamlit.web.server.websocket_headers import _get_websocket_headers
        headers = _get_websocket_headers()
        if headers:
            for key in ("X-Forwarded-For", "X-Real-IP", "X-Client-IP"):
                val = headers.get(key, "").split(",")[0].strip()
                if val and val != "127.0.0.1":
                    return val
    except Exception:
        pass

    # Fallback: use the server's hostname / IP as the instance identifier
    env_ip = os.environ.get("TCM_CLIENT_IP", "")
    if env_ip:
        return env_ip

    try:
        hostname = socket.gethostname()
        return hostname
    except Exception:
        return "default"


# ── Visualization ────────────────────────────────────────────────
def make_gauge(total_score: float, max_score: float = 100.0) -> go.Figure:
    """Gauge chart for total score (0-100 scale, up to 105 with additional)."""
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=total_score,
        delta={"reference": 70, "increasing": {"color": "#2c7744"}},
        number={"font": {"size": 48, "color": "#1a4d2c"}, "suffix": " 分"},
        gauge={
            "axis": {"range": [0, max_score], "tickvals": [20, 40, 60, 80, 100]},
            "bar": {"color": "#2c7744"},
            "steps": [
                {"range": [0, 40], "color": "#ffcdd2"},
                {"range": [40, 60], "color": "#fff9c4"},
                {"range": [60, 80], "color": "#c8e6c9"},
                {"range": [80, 100], "color": "#a5d6a7"},
            ],
            "threshold": {"line": {"color": "#1a4d2c", "width": 3},
                          "thickness": 0.75, "value": total_score},
        },
        title={"text": "综合评分（百分制）", "font": {"size": 20, "color": "#1a4d2c"}},
    ))
    fig.update_layout(height=320, margin=dict(l=30, r=30, t=50, b=20))
    return fig


def make_primary_bar_chart(primary_results: list) -> go.Figure:
    """Horizontal bar chart showing primary indicator scores as percentage of max."""
    df_data = []
    for p in primary_results:
        pct = (p.score / p.weight * 100) if p.weight > 0 else 0
        df_data.append({
            "一级指标": f"{p.id} {p.name}",
            "得分率": round(pct, 1),
            "得分": p.score,
            "满分": p.weight,
        })
    if not df_data:
        return go.Figure()
    df = pd.DataFrame(df_data)
    df = df.sort_values("得分率", ascending=True)

    fig = px.bar(
        df, x="得分率", y="一级指标",
        color="得分率", color_continuous_scale=["#e8f5e9", "#2c7744"],
        text="得分率",
        labels={"得分率": "得分率 (%)", "一级指标": ""},
        title="一级指标得分率对比",
    )
    fig.update_traces(
        texttemplate="%{text:.0f}%", textposition="outside",
        hovertemplate="%{y}<br>得分: %{customdata[0]}/%{customdata[1]}<br>得分率: %{x:.0f}%",
        customdata=df[["得分", "满分"]],
    )
    fig.update_layout(
        xaxis=dict(range=[0, 105], tickvals=[0, 25, 50, 75, 100]),
        coloraxis_showscale=False, height=420,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig


def make_radar_chart(primary_results: list) -> go.Figure:
    """Radar chart using percentage scores for each primary indicator."""
    values = []
    dims = []
    for p in primary_results:
        pct = (p.score / p.weight * 100) if p.weight > 0 else 0
        values.append(round(pct, 1))
        dims.append(f"{p.id} {p.name}")

    if not values:
        return go.Figure()

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]], theta=dims + [dims[0]],
        fill="toself", name="得分率(%)",
        line=dict(color="#2c7744", width=2),
        fillcolor="rgba(44,119,68,0.25)",
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100], tickvals=[20, 40, 60, 80, 100])),
        title=dict(text="一级指标得分率雷达图", x=0.5,
                   font=dict(size=16, color="#1a4d2c")),
        showlegend=False, height=450,
        margin=dict(l=40, r=40, t=60, b=20),
    )
    return fig


def make_compare_radar(batch_results: list) -> go.Figure:
    """Overlay radar chart comparing multiple documents."""
    fig = go.Figure()
    colors = ["#2c7744", "#e67e22", "#2980b9", "#8e44ad", "#c0392b"]

    for idx, (filename, resp) in enumerate(batch_results):
        values = []
        dims = []
        for p in resp.primary_results:
            pct = (p.score / p.weight * 100) if p.weight > 0 else 0
            values.append(round(pct, 1))
            dims.append(f"{p.id} {p.name}")
        if not values:
            continue
        short_name = filename[:30] + ("..." if len(filename) > 30 else "")
        fig.add_trace(go.Scatterpolar(
            r=values + [values[0]], theta=dims + [dims[0]],
            name=short_name,
            line=dict(color=colors[idx % 5], width=2),
            fillcolor=f"rgba{tuple(list(int(colors[idx%5].lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + [0.15])}",
        ))

    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100], tickvals=[20, 40, 60, 80, 100])),
        title=dict(text="多文献得分率对比", x=0.5, font=dict(size=16, color="#1a4d2c")),
        showlegend=True, height=500,
        margin=dict(l=40, r=40, t=60, b=20),
    )
    return fig


def score_color(score: int, max_score: int) -> str:
    """Return a color based on the score/max ratio."""
    if max_score == 0:
        return "#999"
    ratio = score / max_score
    if ratio >= 0.85:
        return "#1b5e20"
    elif ratio >= 0.70:
        return "#43a047"
    elif ratio >= 0.50:
        return "#fb8c00"
    else:
        return "#e53935"


def render_primary_card(primary) -> None:
    """Render a single primary indicator card with secondary detail."""
    pct = (primary.score / primary.weight * 100) if primary.weight > 0 else 0
    color = score_color(primary.score, primary.weight)

    st.markdown(f"""
    <div class="primary-card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-weight:bold;font-size:1.15rem;color:#1a4d2c;">
                {primary.id}、{primary.name}
            </span>
            <span style="font-size:1.3rem;font-weight:bold;color:{color};">
                {primary.score} / {primary.weight} 分 ({pct:.0f}%)
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    for r in primary.secondary_results:
        ratio = (r.score / r.max_score * 100) if r.max_score > 0 else 0
        sc = score_color(r.score, r.max_score)
        st.markdown(f"""
        <div class="score-card">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span class="dimension-name">{r.id} {r.name}</span>
                <span style="font-size:1.1rem;font-weight:bold;color:{sc};">
                    {r.score} / {r.max_score} 分 ({ratio:.0f}%)
                </span>
            </div>
            <div class="evidence-box">\U0001F4CC <b>证据提取：</b>{r.evidence}</div>
            <div style="color:#555;font-size:0.85rem;">\U0001F4AC {r.comment}</div>
        </div>
        """, unsafe_allow_html=True)


# ── Summary text ─────────────────────────────────────────────────
def build_summary(response) -> str:
    """Build downloadable summary text."""
    excluded = getattr(response, 'excluded_indicators', [])
    lines = [
        f"文献名称：{response.doc_name}",
        f"评价时间：{response.timestamp}",
        f"总分：{response.total_score} / 100（基础分 {response.base_score}",
    ]
    bonus_total = sum(a.score for a in response.additional_results) if response.additional_results else 0
    if bonus_total != 0:
        lines[-1] += f"，附加分 {bonus_total:+d}"
    lines[-1] += "）"
    if excluded:
        lines.append(f"已跳过指标：{', '.join(excluded)}（文献不含对应内容，已等比缩放至百分制）")
    lines.append("")

    lines.append("一级指标得分：")
    for p in response.primary_results:
        pct = (p.score / p.weight * 100) if p.weight > 0 else 0
        lines.append(f"  {p.id}、{p.name}: {p.score}/{p.weight} ({pct:.0f}%)")

    lines.append("")
    lines.append("二级指标明细：")
    for p in response.primary_results:
        for r in p.secondary_results:
            lines.append(f"  {r.id} {r.name}: {r.score}/{r.max_score} — {r.comment}")

    lines.append("")
    lines.append("附加分项明细：")
    if response.additional_results:
        for a in response.additional_results:
            sign = "+" if a.score >= 0 else ""
            lines.append(f"  {a.name}: {sign}{a.score}分 — {a.comment}")
        bonus_total = sum(a.score for a in response.additional_results)
        lines.append(f"  附加分合计: {bonus_total:+d}分")
    else:
        lines.append("  无")

    lines.append("")
    lines.append("综合评价：")
    lines.append(response.overall_comment)

    return "\n".join(lines)


# ── Batch comparison table ────────────────────────────────────────
def make_compare_table(batch_results: list) -> pd.DataFrame:
    """Build a DataFrame comparing total/primary scores across files."""
    rows = []
    for filename, resp in batch_results:
        row = {
            "文献": filename[:40] + ("..." if len(filename) > 40 else ""),
            "总分": resp.total_score,
            "基础分": resp.base_score,
        }
        if resp.additional_results:
            row["附加分"] = sum(a.score for a in resp.additional_results) if resp.additional_results else 0
        for p in resp.primary_results:
            pct = (p.score / p.weight * 100) if p.weight > 0 else 0
            row[f"{p.id} ({p.weight}分)"] = f"{p.score}/{p.weight} ({pct:.0f}%)"
        rows.append(row)
    return pd.DataFrame(rows)


def make_secondary_compare_table(batch_results: list) -> pd.DataFrame:
    """Build a DataFrame comparing all secondary indicator scores across files."""
    rows = []
    for filename, resp in batch_results:
        short_name = filename[:30] + ("..." if len(filename) > 30 else "")
        row = {"文献": short_name}
        for p in resp.primary_results:
            for s in p.secondary_results:
                pct = (s.score / s.max_score * 100) if s.max_score > 0 else 0
                row[f"{s.id} {s.name}"] = f"{s.score}/{s.max_score} ({pct:.0f}%)"
        if resp.additional_results:
            for a in resp.additional_results:
                row[f"[附加] {a.name}"] = f"{a.score:+d}"
        rows.append(row)
    return pd.DataFrame(rows)


def make_compare_grouped_bar(batch_results: list) -> go.Figure:
    """Grouped bar chart comparing primary indicator scores across files."""
    fig = go.Figure()
    colors = ["#2c7744", "#e67e22", "#2980b9", "#8e44ad", "#c0392b"]

    for idx, (filename, resp) in enumerate(batch_results):
        short = filename[:25] + ("..." if len(filename) > 25 else "")
        primary_ids = [p.id for p in resp.primary_results]
        scores = [p.score for p in resp.primary_results]
        fig.add_trace(go.Bar(
            x=primary_ids,
            y=scores,
            name=short,
            marker_color=colors[idx % 5],
            text=[f"{s:.0f}" for s in scores],
            textposition="outside",
            hovertemplate="%{x}: %{y:.0f}分<extra>%{data.name}</extra>",
        ))

    fig.update_layout(
        barmode="group",
        title=dict(text="一级指标得分对比", x=0.5, font=dict(size=16, color="#1a4d2c")),
        xaxis=dict(title="", tickfont=dict(size=13)),
        yaxis=dict(title="得分", range=[0, None]),
        height=420,
        margin=dict(l=20, r=20, t=50, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def make_additional_compare_table(batch_results: list) -> pd.DataFrame:
    """Build a DataFrame comparing additional item scores across files."""
    rows = []
    for filename, resp in batch_results:
        short = filename[:30] + ("..." if len(filename) > 30 else "")
        row = {"文献": short}
        if resp.additional_results:
            for a in resp.additional_results:
                row[a.name] = f"{a.score:+d}分"
                row[f"{a.name}(评)"] = a.comment[:40]
        else:
            row["附加分"] = "无"
        rows.append(row)
    return pd.DataFrame(rows)


# ── Single file result display ────────────────────────────────────
def display_single_result(response) -> None:
    """Display evaluation results for a single document."""
    st.markdown("---")
    st.markdown("## \U0001F4CA 评价报告")

    excluded = getattr(response, 'excluded_indicators', [])

    if excluded:
        excluded_names = {
            "2.2": "数据来源", "2.3": "数据分析",
            "3.1": "可操作性", "3.2": "成本效益", "3.3": "风险评估",
        }
        excluded_labels = [f"{eid} {excluded_names.get(eid, '')}" for eid in excluded]
        st.info(
            "ℹ️ 以下指标因文献不含对应内容已自动跳过，"
            f"评分已等比缩放至百分制：{', '.join(excluded_labels)}"
        )

    col_gauge, col_radar = st.columns([1, 1.6], gap="medium")
    with col_gauge:
        max_total = 110 if response.additional_results else 100
        st.plotly_chart(make_gauge(response.total_score, max_total), use_container_width=True)
    with col_radar:
        st.plotly_chart(make_radar_chart(response.primary_results), use_container_width=True)

    st.plotly_chart(make_primary_bar_chart(response.primary_results), use_container_width=True)

    if response.additional_results:
        bonus_total = sum(a.score for a in response.additional_results)
        if bonus_total != 0:
            color = "#43a047" if bonus_total > 0 else "#e53935"
            bonus_lines = "<br>".join(
                f"{a.name}：{a.score:+d}分 — {a.comment}"
                for a in response.additional_results if a.score != 0
            )
            st.markdown(f"""
            <div style="background:#f8f9fa;border-left:5px solid {color};
                        padding:0.8rem;margin:1rem 0;border-radius:4px;">
                <b>附加项</b>（合计 <span style="color:{color};font-weight:bold;">{bonus_total:+d}</span> 分）：<br>
                {bonus_lines}
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("## \U0001F4CB 分指标评价详情")
    for p in response.primary_results:
        render_primary_card(p)

    st.markdown("---")
    st.markdown("## \U0001F4DD 评价摘要")
    summary = build_summary(response)
    base_name = response.doc_name.rsplit(".", 1)[0]
    st.text_area("评价摘要", summary, height=400, disabled=True, label_visibility="collapsed")

    col_dl1, col_dl2, col_dl3 = st.columns(3)
    for col, fmt, label, icon in [
        (col_dl1, "docx", "Word 文档", "\U0001F4D8"),
        (col_dl2, "pdf", "PDF 报告", "\U0001F4C4"),
        (col_dl3, "txt", "纯文本", "\U0001F4DD"),
    ]:
        with col:
            st.download_button(
                f"{icon} 下载{label}",
                generate_report(response, fmt),
                file_name=f"评价报告_{base_name}{get_file_extension(fmt)}",
                mime=get_mime_type(fmt),
            )


# ── Batch result display ──────────────────────────────────────────
def display_batch_results(batch_results: list) -> None:
    """Display evaluation results for multiple documents with comparison view."""
    st.markdown("---")
    st.markdown("## \U0001F4CA 批量评价报告")

    # Summary stats
    scores = [r.total_score for _, r in batch_results]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("文献数量", len(batch_results))
    with col2:
        st.metric("平均总分", f"{sum(scores)/len(scores):.1f}")
    with col3:
        st.metric("最高分", f"{max(scores):.1f}")
    with col4:
        st.metric("最低分", f"{min(scores):.1f}")

    # Comparison table
    st.markdown("### \U0001F4CA 多文献得分对比")
    df = make_compare_table(batch_results)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Overlay radar chart
    if len(batch_results) > 1:
        col_radar, col_bar = st.columns(2, gap="medium")
        with col_radar:
            st.plotly_chart(make_compare_radar(batch_results), use_container_width=True)
        with col_bar:
            st.plotly_chart(make_compare_grouped_bar(batch_results), use_container_width=True)

    # Detailed comparison tables (only when >1 file)
    if len(batch_results) > 1:
        st.markdown("---")
        st.markdown("## \U0001F50D 深度对比分析")

        # Tab 1: Secondary indicator score matrix
        tab1, tab2, tab3 = st.tabs([
            "二级指标得分矩阵", "一级指标得分对比", "附加分项对比",
        ])

        with tab1:
            st.caption("16项二级指标 × 各文献得分（含评分率）")
            df_sec = make_secondary_compare_table(batch_results)
            st.dataframe(
                df_sec.set_index("文献"),
                use_container_width=True,
            )

        with tab2:
            st.caption("7项一级指标得分对比表")
            df_primary = make_compare_table(batch_results)
            st.dataframe(df_primary, use_container_width=True, hide_index=True)

            # Highlight best score per column
            st.caption("上表中 **粗体** 表示该项最优得分")

        with tab3:
            st.caption("附加分项逐文件对比")
            df_add = make_additional_compare_table(batch_results)
            st.dataframe(df_add, use_container_width=True, hide_index=True)

    # Individual file details in expanders
    st.markdown("### \U0001F4C2 各文献评价详情")
    for filename, resp in batch_results:
        r_excluded = getattr(resp, 'excluded_indicators', [])
        r_max_total = 110 if resp.additional_results else 100
        with st.expander(f"{'🟢' if resp.total_score >= 70 else '🟡' if resp.total_score >= 50 else '🔴'} {filename} — {resp.total_score:.1f}分", expanded=False):
            if r_excluded:
                st.info(f"ℹ️ 已跳过指标：{', '.join(r_excluded)}（已等比缩放）")
            col_g, col_r = st.columns([1, 1.6], gap="medium")
            with col_g:
                st.plotly_chart(make_gauge(resp.total_score, r_max_total), use_container_width=True, key=f"gauge_{filename}")
            with col_r:
                st.plotly_chart(make_radar_chart(resp.primary_results), use_container_width=True, key=f"radar_{filename}")

            st.plotly_chart(make_primary_bar_chart(resp.primary_results), use_container_width=True, key=f"bar_{filename}")

            if resp.additional_results:
                bonus_total = sum(a.score for a in resp.additional_results)
                if bonus_total != 0:
                    c = "#43a047" if bonus_total > 0 else "#e53935"
                    bonus_lines = "<br>".join(
                        f"{a.name}：{a.score:+d}"
                        for a in resp.additional_results if a.score != 0
                    )
                    st.markdown(f"""
                    <div style="background:#f8f9fa;border-left:5px solid {c};
                                padding:0.8rem;margin:1rem 0;border-radius:4px;">
                        <b>附加项</b>（合计 <span style="color:{c};font-weight:bold;">{bonus_total:+d}</span>）：<br>
                        {bonus_lines}
                    </div>
                    """, unsafe_allow_html=True)

            for p in resp.primary_results:
                render_primary_card(p)

            summary = build_summary(resp)
            base_name = filename.rsplit(".", 1)[0]
            st.text_area(f"摘要 - {filename}", summary, height=200, disabled=True, label_visibility="collapsed")

            col_bdl1, col_bdl2, col_bdl3 = st.columns(3)
            for col, fmt, label, icon in [
                (col_bdl1, "docx", "Word", "\U0001F4D8"),
                (col_bdl2, "pdf", "PDF", "\U0001F4C4"),
                (col_bdl3, "txt", "TXT", "\U0001F4DD"),
            ]:
                with col:
                    st.download_button(
                        f"{icon} {label}",
                        generate_report(resp, fmt),
                        file_name=f"评价报告_{base_name}{get_file_extension(fmt)}",
                        mime=get_mime_type(fmt),
                        key=f"dl_{fmt}_{filename}",
                    )


# ── Main ─────────────────────────────────────────────────────────
def main():
    # ── Background model warm-up (non-blocking first-load popup) ──
    if "_model_warmed" not in st.session_state:
        st.session_state._model_warmed = False

    # ── Restore persisted auth from localStorage (survives page refresh) ──
    check_persisted_auth()

    # ── Authentication gate ───────────────────────────────────────
    if not is_authenticated():
        render_login_page()
        return

    # ── History record from another tab (query param) ────────────
    if "history_id" in st.query_params:
        from backend.models.schemas import HistoryRecord
        from backend.services.history_service import get_record as get_history_record
        hid = st.query_params["history_id"]
        if isinstance(hid, list):
            hid = hid[0]
        rec = get_history_record(hid)
        if rec:
            st.session_state.eval_response = HistoryRecord(**rec)
            st.session_state.pop("batch_results", None)
        st.query_params.pop("history_id")

    st.markdown("""
    <div class="tcm-header">
        <h1>\U0001F4C4 中医药政策文献智能评价系统</h1>
        <p style="font-size:1.1rem;margin:0;opacity:0.9;">
            基于 GLM-4-Flash 文本推理 + Qwen-VL-Plus 图表识别 | 7项一级指标 16项二级指标 | 百分制评分 | 批量评价 | 可视化报告
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Sidebar ──────────────────────────────────────────────────
    with st.sidebar:
        nav_style = (
            "display:block;text-decoration:none;"
            "background:linear-gradient(135deg,#2c7744,#1a4d2c);color:#fff;"
            "padding:12px 16px;border-radius:8px;text-align:center;"
            "font-weight:600;font-size:15px;margin-bottom:10px;"
        )
        st.markdown(f"""
        <a href="/" target="_self" style="{nav_style}">
        🏠 主界面
        </a>
        <a href="/优秀文献展馆" target="_self" style="{nav_style}">
        🏆 优秀文献展馆
        </a>
        <a href="/ecoeval/" target="_blank" style="{nav_style}">
        📊 卫生经济学综合评价 →
        </a>
        """, unsafe_allow_html=True)

        st.divider()
        st.header("\U0001F4C2 文献上传")
        st.markdown("支持 **PDF** 和 **Word (DOCX)** 格式，单次最多 **5 个文件**")
        uploaded_files = st.file_uploader(
            "选择文献文件（可多选）", type=["pdf", "docx"],
            accept_multiple_files=True, label_visibility="collapsed",
        )

        # Enforce 5-file limit
        if uploaded_files and len(uploaded_files) > 5:
            st.error("单次最多上传 5 个文件")
            uploaded_files = uploaded_files[:5]

        _upload_result = None  # placeholder for future HTTP component

        st.divider()
        st.header("\U0001F4CA 评价体系")
        st.markdown("**7项一级指标 · 16项二级指标 · 百分制**")
        st.caption("「三、政策建议可行性」为可选项，系统根据文献内容自动决定是否纳入")
        for p in PRIMARY_INDICATORS:
            optional_tag = " \U0001F6C8️ 可选" if p['id'] == '三' else ""
            with st.expander(f"{p['id']}、{p['name']}（{p['weight']}分）{optional_tag}"):
                for s in p["secondary"]:
                    st.markdown(f"- {s['id']} **{s['name']}**（{s['max_score']}分）")

        st.divider()
        # Background model warmup (triggers on first render, cached thereafter)
        if not st.session_state._model_warmed:
            with st.spinner("正在初始化 API 客户端..."):
                _load_model_cached()
                st.session_state._model_warmed = True
        st.caption("推理引擎: 智谱 GLM-4 (云端 API)")

        if st.button("\U0001F5D1️ 清除缓存与结果", use_container_width=True):
            st.session_state.pop("eval_response", None)
            st.session_state.pop("batch_results", None)
            st.session_state.pop("doc_text", None)
            clear_cache()
            st.rerun()

        st.divider()
        st.header("\U0001F4DC 历史记录")
        current_user = st.session_state.get("authenticated_user", "")
        history = _load_history_cached(current_user)
        total_count = len(history)
        st.caption(f"当前用户: {current_user} | 共 {total_count} 条记录")
        if not history:
            st.caption("暂无历史评价记录")
        else:
            for h in history:
                col1, col2 = st.columns([3, 1])
                with col1:
                    label = f"{h.get('doc_name', '')[:30]} — {h.get('total_score', 0):.1f}分"
                    st.markdown(
                        f'<a href="?history_id={h["id"]}" target="_blank" '
                        f'style="display:inline-block;width:100%;padding:0.35rem 0.6rem;'
                        f'background:#f0f2f6;border:1px solid #d0d5dd;border-radius:0.5rem;'
                        f'color:#1a1a1a;text-decoration:none;font-size:0.88rem;'
                        f'text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"'
                        f'>{label}</a>',
                        unsafe_allow_html=True,
                    )
                with col2:
                    if st.button("\U0001F5D1️", key=f"del_{h['id']}", help="删除"):
                        delete_record(h["id"])
                        st.rerun()
                st.caption(h.get("timestamp", ""))

        # ── Logout ──────────────────────────────────────────────
        st.divider()
        st.caption(f"当前用户: {st.session_state.get('authenticated_user', 'unknown')}")
        if st.button("登出 / Logout", use_container_width=True):
            logout()
            st.rerun()

    # ── Landing page ────────────────────────────────────────────
    has_upload = uploaded_files and len(uploaded_files) > 0
    has_single = "eval_response" in st.session_state
    has_batch = "batch_results" in st.session_state

    if not has_upload and not has_single and not has_batch:
        st.info("\U0001F449 请在左侧上传中医药政策研究文献（PDF/DOCX，最多5个），系统将自动进行多维度评分")
        st.markdown("""
        ### 使用说明
        1. **上传文献** — 在左侧边栏上传 PDF 或 Word 格式的中医药政策研究文献（支持批量，最多5个）
        2. **自动评价** — 系统调用 GLM-4-Flash 文本推理 + Qwen-VL-Plus 图表识别，按 **7项一级指标 16项二级指标** 逐一评分
        3. **生成报告** — 输出雷达图、柱状图、分指标证据卡片等可视化报告
        4. **批量对比** — 多文献同步对比，雷达图叠加显示各文献得分差异

        ### 评价体系（百分制 + 附加分）
        | 一级指标 | 权重 | 二级指标数 |
        |----------|------|-----------|
        | 一、研究背景与问题界定 | 15分 | 2项 |
        | 二、研究方法与数据 | 20分 | 3项 |
        | 三、政策建议可行性 | 18分 | 3项 |
        | 四、逻辑结构与论证 | 12分 | 2项 |
        | 五、创新性与前瞻性 | 10分 | 2项 |
        | 六、语言表达与格式 | 10分 | 2项 |
        | 七、实际应用价值 | 15分 | 2项 |
        | **附加项** | 0-15分 | 学科适配性、方法学复杂度、政策时效性、图表质量 |
        """)
        return

    # ── Batch upload & evaluation flow ─────────────────────────
    if has_upload:
        is_batch = len(uploaded_files) > 1

        st.markdown("### \U0001F4C4 已上传文献列表")
        for i, f in enumerate(uploaded_files):
            st.markdown(
                f"""<div class="batch-file-card">
                <b>{i+1}.</b> {f.name} <span style="color:#888;">({f.size/1024:.0f} KB)</span>
                </div>""",
                unsafe_allow_html=True,
            )

        btn_label = f"\U0001F680 开始批量评价（{len(uploaded_files)} 个文件）" if is_batch else "\U0001F680 开始智能评价（16项指标）"
        if st.button(btn_label, type="primary", use_container_width=True):
            with st.spinner("正在加载模型..."):
                _load_model_cached()

            batch_results = []
            total_files = len(uploaded_files)
            overall_progress = st.progress(0, f"准备处理 {total_files} 个文件...")
            file_status = st.empty()

            for idx, uploaded_file in enumerate(uploaded_files):
                file_status.markdown(f"**\U0001F4D6 正在处理 ({idx+1}/{total_files}): {uploaded_file.name}**")

                content = uploaded_file.read()
                file_hash = hashlib.md5(content).hexdigest()

                # Dedup
                cached = lookup_cached_result(file_hash)
                if cached:
                    from backend.models.schemas import HistoryRecord
                    response = HistoryRecord(**cached)
                    file_status.markdown(f"\U0001F4E6 **缓存命中** — `{uploaded_file.name}` 此前已评价，直接返回历史结果")
                    batch_results.append((response.doc_name, response))
                    overall_progress.progress((idx + 1) / total_files, f"已完成 {idx+1}/{total_files} (缓存)")
                    continue

                # Parse
                try:
                    from io import BytesIO
                    doc_name, doc_text = parse_document(BytesIO(content), uploaded_file.name)
                except Exception as exc:
                    st.error(f"❌ 解析失败 [{uploaded_file.name}]: {exc}")
                    overall_progress.progress((idx + 1) / total_files, f"已处理 {idx+1}/{total_files} ({uploaded_file.name} 解析失败)")
                    continue

                if not doc_text.strip():
                    st.error(f"❌ 文献内容为空 [{uploaded_file.name}]")
                    overall_progress.progress((idx + 1) / total_files, f"已处理 {idx+1}/{total_files} ({uploaded_file.name} 内容为空)")
                    continue

                # Evaluate with per-indicator progress
                indicator_progress = st.progress(0, f"正在评价 {doc_name}...")
                indicator_status = st.empty()

                def progress_cb(current, total):
                    pct = current / total
                    indicator_status.text(f"\U0001F50D {doc_name} | 二级指标 [{current}/{total}]")
                    indicator_progress.progress(pct)

                response = evaluate_document(
                    text=doc_text, doc_name=doc_name,
                    max_chars=MAX_DOC_CHARS, include_additional=True,
                    progress_callback=progress_cb,
                )

                indicator_progress.progress(1.0)
                indicator_status.text(f"✅ {doc_name} 评价完成")
                time.sleep(0.3)
                indicator_progress.empty()
                indicator_status.empty()

                save_to_history(
                    record_id=response.id, timestamp=response.timestamp,
                    doc_name=response.doc_name, base_score=response.base_score,
                    total_score=response.total_score,
                    scale_factor=response.scale_factor,
                    excluded_indicators=response.excluded_indicators,
                    primary_results=response.primary_results,
                    additional_results=response.additional_results,
                    overall_comment=response.overall_comment,
                    ip_address=get_client_ip(),
                    username=st.session_state.get("authenticated_user", ""),
                    filename=doc_name, file_hash=file_hash,
                )

                batch_results.append((doc_name, response))
                overall_progress.progress((idx + 1) / total_files, f"已完成 {idx+1}/{total_files}")

            file_status.empty()
            overall_progress.empty()

            if batch_results:
                if len(batch_results) == 1:
                    st.session_state.eval_response = batch_results[0][1]
                    st.session_state.pop("batch_results", None)
                else:
                    st.session_state.batch_results = batch_results
                    st.session_state.pop("eval_response", None)
                st.rerun()
            else:
                st.error("所有文件处理失败，请检查文件格式和内容")

    # ── Display single result ───────────────────────────────────
    if has_single and not has_batch:
        response = st.session_state.eval_response
        if not hasattr(response, 'primary_results'):
            st.warning("历史数据格式不兼容，请重新评价")
            return

        # Show filename banner
        st.success(f"✅ 文献: **{response.doc_name}**")
        display_single_result(response)

    # ── Display batch results ───────────────────────────────────
    if has_batch:
        batch_results = st.session_state.batch_results
        filenames = [name for name, _ in batch_results]
        st.success(f"✅ 批量评价完成: {', '.join(filenames)}")
        display_batch_results(batch_results)


main_page = st.Page(main, title="主界面", default=True)
gallery_page = st.Page("pages/1_🏆_优秀文献展馆.py", title="🏆 优秀文献展馆")
pg = st.navigation([main_page, gallery_page], position="hidden")
pg.run()
