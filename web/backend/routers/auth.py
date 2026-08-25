"""Authentication endpoints for the HTML frontend.

Token format is compatible with the Streamlit frontend (auth_service.py):
HMAC-signed JSON payload `{u: username, exp: timestamp}` → `payload.signature`.
"""

import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.database import verify_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

_TOKEN_TTL: int = 86400 * 7  # 7 days


class LoginRequest(BaseModel):
    username: str
    password: str


def _secret_key() -> bytes:
    secret = os.environ.get("TCM_SECRET_KEY", "tcm-default-secret-key-2024")
    return hashlib.sha256(secret.encode()).digest()


def make_token(username: str) -> str:
    payload = json.dumps({"u": username, "exp": int(time.time()) + _TOKEN_TTL})
    sig = hmac.new(_secret_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str) -> str | None:
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


@router.post("/login", summary="登录并获取令牌")
def login(req: LoginRequest) -> dict:
    if not verify_user(req.username, req.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = make_token(req.username)
    return {"token": token, "username": req.username}


@router.get("/verify", summary="校验令牌")
def verify(token: str) -> dict:
    username = verify_token(token)
    if username is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    return {"username": username}
