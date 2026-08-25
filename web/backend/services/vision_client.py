"""Vision-language client — sends document images to Qwen-VL for description.

Uses DashScope's OpenAI-compatible endpoint for multimodal chat.
Each image is described in terms of type (图表/照片/截图/其他) and content.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Optional

from openai import OpenAI

from backend.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    QWEN_VL_MODEL,
)

logger = logging.getLogger(__name__)

_vision_client: OpenAI | None = None

_DESCRIBE_SYSTEM_PROMPT = """你是一位学术文献插图分析专家。请分析以下图片，用中文描述其内容和类型。

【输出要求】
严格按JSON数组格式输出，每张图片对应一个对象：
[
    {"index": 0, "type": "图表类型", "description": "图片内容描述"},
    ...
]

type 取值：表格、统计图、流程图、照片、截图、示意图、其他
description：简洁描述图片展示的内容（50—150字），如果是表格/统计图，提取其中关键数据"""


def _get_vision_client() -> OpenAI:
    global _vision_client
    if _vision_client is not None:
        return _vision_client
    if not DASHSCOPE_API_KEY:
        raise RuntimeError(
            "未设置 DASHSCOPE_API_KEY 环境变量。"
            "请前往 https://dashscope.console.aliyun.com/ 获取 API Key。"
        )
    _vision_client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )
    logger.info("DashScope vision client initialized — model: %s", QWEN_VL_MODEL)
    return _vision_client


def _pil_to_data_uri(img, format: str = "jpeg", quality: int = 75) -> str:
    """Convert a PIL Image to a data URI string."""
    buf = io.BytesIO()
    img.save(buf, format=format, quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/{format};base64,{b64}"


def describe_images(
    images: list,
    context: str = "",
) -> list[dict]:
    """Send images to Qwen-VL for description.

    Args:
        images: List of PIL Images.
        context: Optional document context (e.g., filename or title).

    Returns:
        List of dicts: [{"index": 0, "type": "...", "description": "..."}, ...]
    """
    if not images:
        return []

    client = _get_vision_client()

    # Build multimodal content
    content: list[dict] = [
        {"type": "text", "text": _DESCRIBE_SYSTEM_PROMPT},
    ]

    if context:
        content[0]["text"] += f"\n文献名称：{context}"

    for img in images:
        data_uri = _pil_to_data_uri(img)
        content.append({
            "type": "image_url",
            "image_url": {"url": data_uri},
        })

    messages = [{"role": "user", "content": content}]

    try:
        response = client.chat.completions.create(
            model=QWEN_VL_MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0.1,
        )
        raw = response.choices[0].message.content
        if not raw:
            logger.warning("Empty response from vision model")
            return []

        parsed = _parse_image_descriptions(raw, len(images))
        logger.info("Described %d/%d images", len(parsed), len(images))
        return parsed
    except Exception as exc:
        logger.exception("Vision API call failed: %s", exc)
        return []


def _parse_image_descriptions(raw: str, expected_count: int) -> list[dict]:
    """Parse the JSON response from the vision model."""
    # Try extracting from ```json block
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    # Try extracting from first [ to last ]
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        raw = m.group(0)

    try:
        results = json.loads(raw)
        if isinstance(results, list):
            return [{
                "index": r.get("index", i),
                "type": r.get("type", "其他"),
                "description": r.get("description", ""),
            } for i, r in enumerate(results)]
    except json.JSONDecodeError:
        logger.warning("Failed to parse vision response as JSON: %s...", raw[:200])

    # Fallback: return raw text as single description
    if expected_count == 1:
        return [{"index": 0, "type": "其他", "description": raw[:500]}]
    return []


def describe_single_image(img, context: str = "") -> dict:
    """Convenience wrapper for describing a single image.

    Returns:
        {"type": "...", "description": "..."} or empty dict on failure.
    """
    results = describe_images([img], context=context)
    if results:
        return {"type": results[0]["type"], "description": results[0]["description"]}
    return {}
