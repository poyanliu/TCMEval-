"""Stage 2 — Model Inference for the Intelligent Evaluation Agent.

Three-stage pipeline:
  1. Prompt Construction (prompt_builder)  → builds structured prompts
  2. Model Inference (this module)         → calls ZhipuAI GLM-4 API
  3. Response Parsing (evaluation_service) → orchestrates + aggregates

API mode — uses ZhipuAI's OpenAI-compatible endpoint.
No local GPU/model required. Works within 2GB memory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

from openai import OpenAI

from backend.config import (
    ZHIPUAI_API_KEY,
    ZHIPUAI_BASE_URL,
    ZHIPUAI_MODEL,
    ZHIPUAI_FALLBACK_MODELS,
    MAX_NEW_TOKENS,
    TEMPERATURE,
    TOP_P,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Module-level client cache
# ═══════════════════════════════════════════════════════════════════════

_client: OpenAI | None = None
_current_model: str = ZHIPUAI_MODEL


# ═══════════════════════════════════════════════════════════════════════
# Retry configuration
# ═══════════════════════════════════════════════════════════════════════

MAX_RETRIES: int = 3
BASE_DELAY_SECONDS: float = 1.0
MAX_DELAY_SECONDS: float = 30.0


# ═══════════════════════════════════════════════════════════════════════
# Client initialization  (replaces local model loading)
# ═══════════════════════════════════════════════════════════════════════

def _build_client() -> OpenAI:
    """Create an OpenAI client pointed at ZhipuAI's endpoint."""
    api_key = ZHIPUAI_API_KEY
    if not api_key:
        raise RuntimeError(
            "未设置 ZHIPUAI_API_KEY 环境变量。\n"
            "请前往 https://open.bigmodel.cn/ 注册并获取 API Key，\n"
            "然后设置: export ZHIPUAI_API_KEY=你的key\n"
            "然后到 .env 文件中设置 ZHIPUAI_API_KEY。"
        )
    return OpenAI(
        api_key=api_key,
        base_url=ZHIPUAI_BASE_URL,
    )


def load_model() -> OpenAI:
    """Initialize and cache the ZhipuAI API client.

    Replaces the old local-model loading. Safe to call multiple times —
    returns the cached client after first initialization.

    Returns:
        Configured OpenAI client pointed at ZhipuAI endpoint.
    """
    global _client
    if _client is not None:
        return _client
    _client = _build_client()
    logger.info("ZhipuAI client initialized — model: %s, endpoint: %s",
                ZHIPUAI_MODEL, ZHIPUAI_BASE_URL)
    return _client


def get_tokenizer():
    """Backward-compatible stub — API mode doesn't use a local tokenizer."""
    load_model()  # ensure client is ready
    return None


# ═══════════════════════════════════════════════════════════════════════
# Inference
# ═══════════════════════════════════════════════════════════════════════

def _try_chat_completion(
    client: OpenAI,
    messages: list[dict],
    model: str,
    max_new_tokens: int = MAX_NEW_TOKENS,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
) -> str:
    """Make a single API call with the given model."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        stream=False,
    )
    content = response.choices[0].message.content
    return content.strip() if content else ""


def call_model(
    prompt: str,
    max_new_tokens: int = MAX_NEW_TOKENS,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
) -> str:
    """Run inference via ZhipuAI API.

    Args:
        prompt: The formatted evaluation prompt.
        max_new_tokens: Max tokens to generate.
        temperature: Sampling temperature.
        top_p: Nucleus sampling threshold.

    Returns:
        Raw decoded response text.
    """
    client = load_model()
    messages = [{"role": "user", "content": prompt}]

    # Try primary model, fall back on failure
    models_to_try = [ZHIPUAI_MODEL] + [
        m for m in ZHIPUAI_FALLBACK_MODELS if m != ZHIPUAI_MODEL
    ]

    last_error: Exception | None = None
    for model in models_to_try:
        try:
            result = _try_chat_completion(
                client, messages, model,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            if result:
                if model != ZHIPUAI_MODEL:
                    logger.info("Fell back to model %s", model)
                return result
        except Exception as exc:
            last_error = exc
            logger.warning("Model %s failed: %s", model, exc)
            continue

    raise RuntimeError(
        f"All models failed. Last error: {last_error}"
    )


def call_model_with_retry(
    prompt: str,
    max_retries: int = MAX_RETRIES,
    max_new_tokens: int = MAX_NEW_TOKENS,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
) -> str:
    """Run inference with exponential-backoff retry.

    Args:
        prompt: The formatted evaluation prompt.
        max_retries: Maximum retry attempts (default 3).
        max_new_tokens: Max tokens to generate.
        temperature: Sampling temperature.
        top_p: Nucleus sampling threshold.

    Returns:
        Raw decoded response text.

    Raises:
        RuntimeError: All retries exhausted.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = call_model(
                prompt,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            if result.strip():
                return result
            logger.warning(
                "Empty response on attempt %d/%d, retrying…",
                attempt + 1, max_retries + 1,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Inference failed on attempt %d/%d: %s",
                attempt + 1, max_retries + 1, exc,
            )

        if attempt < max_retries:
            delay = min(
                BASE_DELAY_SECONDS * (2 ** attempt),
                MAX_DELAY_SECONDS,
            )
            logger.info("Retrying in %.1fs…", delay)
            time.sleep(delay)

    raise RuntimeError(
        f"Inference failed after {max_retries + 1} attempts. "
        f"Last error: {last_error}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Async inference
# ═══════════════════════════════════════════════════════════════════════

async def call_model_async(
    prompt: str,
    max_new_tokens: int = MAX_NEW_TOKENS,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
) -> str:
    """Async wrapper — offloads the blocking API call to a thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: call_model(
            prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
        ),
    )


async def call_model_with_retry_async(
    prompt: str,
    max_retries: int = MAX_RETRIES,
    max_new_tokens: int = MAX_NEW_TOKENS,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
) -> str:
    """Async inference with retry."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: call_model_with_retry(
            prompt,
            max_retries=max_retries,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# Batch inference
# ═══════════════════════════════════════════════════════════════════════

async def batch_infer_async(
    prompts: list[str],
    max_concurrency: int = 3,
    **gen_kwargs,
) -> list[str]:
    """Run multiple prompts concurrently with a concurrency limit.

    Uses a semaphore to avoid rate-limiting the API.

    Args:
        prompts: List of formatted prompt strings.
        max_concurrency: Max simultaneous API calls.
        **gen_kwargs: Passed through to call_model_async.

    Returns:
        List of raw responses in the same order as prompts.
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _bounded(prompt: str) -> str:
        async with semaphore:
            return await call_model_async(prompt, **gen_kwargs)

    tasks = [_bounded(p) for p in prompts]
    return await asyncio.gather(*tasks)


# ═══════════════════════════════════════════════════════════════════════
# Structured output validation
# ═══════════════════════════════════════════════════════════════════════

def validate_json_structure(raw: str) -> bool:
    """Quick check: does the response contain anything JSON-like?"""
    if not raw or not raw.strip():
        return False
    if "{" not in raw or "}" not in raw:
        return False
    if "score" not in raw.lower() and '"score"' not in raw:
        return False
    return True


def check_output_quality(raw: str) -> dict:
    """Assess inference output quality and return diagnostic info."""
    info: dict = {
        "valid_json_structure": False,
        "has_score_key": False,
        "has_evidence_key": False,
        "response_length": len(raw) if raw else 0,
        "estimated_confidence": 0.0,
    }
    if not raw:
        return info

    info["valid_json_structure"] = "{" in raw and "}" in raw
    info["has_score_key"] = bool(re.search(r'"score"\s*:', raw))
    info["has_evidence_key"] = bool(re.search(r'"evidence"\s*:', raw))

    confidence = 0.0
    if info["valid_json_structure"]:
        confidence += 0.3
    if info["has_score_key"]:
        confidence += 0.35
    if info["has_evidence_key"]:
        confidence += 0.2
    if info["response_length"] > 50:
        confidence += 0.15
    info["estimated_confidence"] = min(confidence, 1.0)
    return info


# ═══════════════════════════════════════════════════════════════════════
# Token counting (estimation — no local tokenizer in API mode)
# ═══════════════════════════════════════════════════════════════════════

def count_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a character-based heuristic suitable for Chinese+English mixed text.
    Chinese chars ≈ 1.5 tokens, ASCII ≈ 0.25 tokens.

    Args:
        text: The text to count tokens for.

    Returns:
        Estimated token count.
    """
    chinese_chars = len(re.findall(r"[一-鿿㐀-䶿]", text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.25)


def check_context_fit(prompt: str, max_context: int = 128000) -> tuple[bool, int]:
    """Check whether a prompt fits within the model's context window.

    GLM-4-Flash supports 128K context.

    Args:
        prompt: The full prompt text.
        max_context: Model's max context length in tokens.

    Returns:
        Tuple of (fits, estimated_token_count).
    """
    tokens = count_tokens(prompt)
    fits = tokens <= max_context - MAX_NEW_TOKENS
    return fits, tokens


# ═══════════════════════════════════════════════════════════════════════
# Cache management (no-op in API mode)
# ═══════════════════════════════════════════════════════════════════════

def clear_cache() -> None:
    """No-op in API mode — no local model to release."""
    pass


def get_vram_usage() -> float:
    """Return -1 in API mode (no local GPU usage)."""
    return -1.0
