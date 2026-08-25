"""Authentication service for the Streamlit frontend.

Session persistence
  st.session_state survives reruns within the same WebSocket connection
  but is lost on a hard page refresh (browser F5).  To survive refreshes
  we store a time-limited, HMAC-signed token in a browser cookie that the
  server can read on the very first request — no async JS bridge needed.

Credentials
  PBKDF2-HMAC-SHA256 (stdlib hashlib), stored in the SQLite users table.
  Test accounts are seeded automatically on first database initialization.
"""

import hashlib
import hmac
import json
import os
import time
from urllib.parse import unquote

import streamlit as st

# ── Session-state key ───────────────────────────────────────────────
_AUTH_KEY: str = "authenticated_user"
_TOKEN_TTL: int = 86400 * 7  # 7 days


def _secret_key() -> bytes:
    """Derive a signing key from a fixed secret (never exposed)."""
    secret = os.environ.get("TCM_SECRET_KEY", "tcm-default-secret-key-2024")
    return hashlib.sha256(secret.encode()).digest()


def _make_token(username: str) -> str:
    """Return a time-limited HMAC-signed token for localStorage."""
    payload = json.dumps({
        "u": username,
        "exp": int(time.time()) + _TOKEN_TTL,
    })
    sig = hmac.new(_secret_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_token(token: str) -> str | None:
    """Return the username if the token is valid, else None."""
    try:
        payload_str, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret_key(), payload_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(payload_str)
        if payload["exp"] < time.time():
            return None
        return payload["u"]
    except Exception:
        return None


# ── Public API ──────────────────────────────────────────────────────
def is_authenticated() -> bool:
    """Return True if the current browser session has a logged-in user."""
    return _AUTH_KEY in st.session_state and st.session_state[_AUTH_KEY] is not None


def login(username: str, password: str) -> bool:
    """Validate credentials against the database and persist authentication."""
    from backend.services.database import verify_user

    if not verify_user(username, password):
        return False

    st.session_state[_AUTH_KEY] = username
    token = _make_token(username)
    st.markdown(
        f"""<script>document.cookie='tcm_auth='+encodeURIComponent('{token}')+';path=/;max-age={_TOKEN_TTL};SameSite=Lax';</script>""",
        unsafe_allow_html=True,
    )
    return True


def logout() -> None:
    """Clear authentication and all evaluation state from the session."""
    st.session_state.pop(_AUTH_KEY, None)
    st.session_state.pop("eval_response", None)
    st.session_state.pop("batch_results", None)
    st.session_state.pop("doc_text", None)
    # Clear persisted cookie
    st.markdown(
        """<script>document.cookie='tcm_auth=;path=/;max-age=0';</script>""",
        unsafe_allow_html=True,
    )


def check_persisted_auth() -> None:
    """Restore authentication from a persisted cookie on first request.

    Cookies are sent by the browser with every HTTP request, so the
    server can verify the token synchronously on the very first script
    run — no async iframe/postMessage bridge that can miss timing.
    """
    if is_authenticated():
        return

    token = unquote(st.context.cookies.get("tcm_auth", ""))
    if token:
        username = _verify_token(token)
        if username:
            st.session_state[_AUTH_KEY] = username
            st.rerun()


# ── Login page UI ───────────────────────────────────────────────────
def render_login_page() -> None:
    """Render a centered login form with TCM branding."""

    # Sidebar - visible even when not logged in
    nav_style = (
        "display:block;text-decoration:none;"
        "background:linear-gradient(135deg,#2c7744,#1a4d2c);color:#fff;"
        "padding:12px 16px;border-radius:8px;text-align:center;"
        "font-weight:600;font-size:15px;margin-bottom:10px;"
    )
    with st.sidebar:
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

    col_left, col_center, col_right = st.columns([1, 2, 1])

    with col_center:
        st.markdown("""
        <div class="tcm-header">
            <h1>&#x1F4C4; 中医药政策文献智能评价系统</h1>
            <p style="font-size:1.1rem;margin:0;opacity:0.9;">
                基于 GLM-4-9B 大模型 | 7项一级指标 16项二级指标 | 百分制评分
            </p>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        username = st.text_input(
            "用户名 / Username",
            key="auth_username_input",
            placeholder="请输入用户名",
        )
        password = st.text_input(
            "密码 / Password",
            type="password",
            key="auth_password_input",
            placeholder="请输入密码",
        )

        if st.button("登录 / Login", type="primary", use_container_width=True):
            if login(username, password):
                st.rerun()
            else:
                st.error("用户名或密码错误 / Invalid username or password")
