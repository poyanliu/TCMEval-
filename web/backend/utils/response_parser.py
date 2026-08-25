"""LLM response parser — extracts structured JSON from model output.

Handles common failure modes: markdown wrapping, Chinese prefixes,
partial JSON, and malformed output. The fallback chain is ordered
from most-to-least reliable extraction method.
"""

import json
import re
from typing import Optional


# ── Type alias ─────────────────────────────────────────────────────
ParsedResult = dict[str, object]  # {"score": int, "evidence": str, "comment": str}


# ── Extraction chain ───────────────────────────────────────────────
def _try_markdown_block(raw: str) -> Optional[ParsedResult]:
    """Extract JSON wrapped in ```json ... ``` fences."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _try_score_objects(raw: str) -> Optional[ParsedResult]:
    """Find the first JSON object containing a "score" key."""
    pattern = r'\{[^{}]*"score"\s*:\s*\d+[^{}]*\}'
    for m in re.finditer(pattern, raw, re.DOTALL):
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            continue
    return None


def _try_clean_parse(raw: str) -> Optional[ParsedResult]:
    """Strip known Chinese prefixes then attempt full parse."""
    cleaned = re.sub(
        r"^(?:好的|以下是|根据|这是|JSON|输出)\s*[：:]*\s*",
        "",
        raw.strip(),
    )
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _try_bracket_balanced(raw: str) -> Optional[ParsedResult]:
    """Attempt to extract JSON by balancing braces."""
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ── Score extraction ───────────────────────────────────────────────
_SCORE_PATTERNS: list[re.Pattern] = [
    re.compile(r'"score"\s*:\s*(\d+)'),
    re.compile(r"(?:评?分|score|得分)\s*[:：]\s*(\d+)"),
    re.compile(r"(\d+)\s*分"),
]


def _extract_score(raw: str) -> int:
    """Best-effort score extraction from raw text.

    Score range is NOT clamped here — the caller applies indicator-
    specific bounds (e.g. 0-9 for secondary, -5 to +5 for additional).
    """
    for pat in _SCORE_PATTERNS:
        m = pat.search(raw)
        if m:
            return int(m.group(1))
    # Also check for negative scores in additional items
    neg_match = re.search(r'"score"\s*:\s*(-?\d+)', raw)
    if neg_match:
        return int(neg_match.group(1))
    return 0


def _extract_field(raw: str, field: str, max_len: int) -> str:
    """Extract a string field value from raw JSON-ish text."""
    # Try quoted value first
    m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw, re.DOTALL)
    if m:
        return m.group(1)[:max_len]
    # Try unquoted value
    m = re.search(rf'"{field}"\s*:\s*(.+?)(?:,|}}|\n)', raw, re.DOTALL)
    if m:
        val = m.group(1).strip().strip('"')
        return val[:max_len]
    return "解析失败"


# ── Public API ─────────────────────────────────────────────────────
def parse_llm_response(raw: str) -> ParsedResult:
    """Extract {score, evidence, comment} from an LLM response string.

    Applies a chain of extraction strategies, falling back to regex
    field extraction when JSON parsing fails.

    Args:
        raw: The raw text output from the model.

    Returns:
        Dict with keys: score (int 1-10), evidence (str), comment (str).
    """
    if not raw or not raw.strip():
        return {"score": 0, "evidence": "模型无输出", "comment": "评估失败"}

    for strategy in (_try_markdown_block, _try_score_objects,
                     _try_clean_parse, _try_bracket_balanced):
        result = strategy(raw)
        if result and "score" in result:
            score = int(result["score"])
            return {
                "score": score,
                "evidence": str(result.get("evidence", ""))[:500],
                "comment": str(result.get("comment", ""))[:200],
            }

    # Last-resort fallback
    return {
        "score": _extract_score(raw),
        "evidence": _extract_field(raw, "evidence", 500),
        "comment": _extract_field(raw, "comment", 200),
    }


def parse_batch_results(raw_outputs: list[str]) -> list[ParsedResult]:
    """Parse a batch of LLM responses."""
    return [parse_llm_response(r) for r in raw_outputs]
