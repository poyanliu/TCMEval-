"""Stage 3 — Response Parsing & Orchestration for the Intelligent Evaluation Agent.

Three-stage pipeline:
  1. Prompt Construction (prompt_builder)  → builds structured prompts
  2. Model Inference (llm_client)         → runs LLM with retry + validation
  3. Response Parsing & Orchestration (this module) → parses, aggregates, reflects

Agent capabilities:
  - Multi-role expert panel with hierarchical review
  - Persistent memory for scoring calibration across evaluations
  - Reflection-and-refine loop: self-check consistency, auto-correct outliers
  - Quality gates with confidence-based re-evaluation
  - Parallel async execution for independent indicators
  - Cross-indicator consistency validation
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from collections.abc import Callable

from shared.constants import (
    PRIMARY_INDICATORS,
    ALL_SECONDARY_INDICATORS,
    SECONDARY_TO_PRIMARY,
    OVERALL_THRESHOLDS,
    ADDITIONAL_ITEMS,
    ADDITIONAL_MAX_TOTAL,
    get_active_primary_indicators,
    get_active_secondary_indicators,
    get_excluded_indicator_ids,
    SecondaryIndicator,
)
from backend.models.schemas import (
    SecondaryResult,
    PrimaryResult,
    AdditionalResult,
    EvaluationResponse,
)
from backend.services.prompt_builder import (
    build_secondary_prompt,
    build_additional_prompt,
    assess_document_complexity,
    detect_applicable_indicators,
)
from backend.services.llm_client import (
    call_model,
    call_model_with_retry,
    call_model_async,
    call_model_with_retry_async,
    batch_infer_async,
    validate_json_structure,
    check_output_quality,
    count_tokens,
)
from backend.utils.response_parser import parse_llm_response
from backend.utils.document_parser import truncate_text

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Agent Role System — multi-faceted expert panel
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class AgentRole:
    """A single expert role in the evaluation panel."""
    name: str
    title: str
    expertise: str
    focus_indicators: list[str]  # indicator IDs this role specializes in
    review_weight: float = 1.0   # weight in consensus scoring


# The expert panel — three roles covering different evaluation dimensions
AGENT_ROLES: list[AgentRole] = [
    AgentRole(
        name="methodologist",
        title="研究方法论专家",
        expertise=(
            "15年公共政策研究方法论经验，精通定量分析（计量经济学、统计学）"
            "和定性方法（案例研究、扎根理论）。专注评估研究设计的科学性、"
            "数据来源的可靠性、分析过程的严谨性。"
        ),
        focus_indicators=["2.1", "2.2", "2.3", "4.1", "4.2"],
        review_weight=1.2,  # higher weight on methodology indicators
    ),
    AgentRole(
        name="policy_analyst",
        title="中医药政策分析师",
        expertise=(
            "12年中医药政策研究经验，曾在国家中医药管理局政策研究室任职。"
            "擅长政策背景分析、建议可行性评估、实施路径设计。"
            "参与过《中医药法》实施评估等多项国家级政策研究课题。"
        ),
        focus_indicators=["1.1", "1.2", "3.1", "3.2", "3.3", "7.1", "7.2"],
        review_weight=1.2,
    ),
    AgentRole(
        name="chief_reviewer",
        title="评审委员会主席",
        expertise=(
            "20年学术期刊主编经验，评审过上千篇政策研究论文。"
            "擅长综合判断文献的整体质量、创新性、表达能力，"
            "并能协调不同评审专家的意见分歧，做出最终裁定。"
        ),
        focus_indicators=["5.1", "5.2", "6.1", "6.2"],
        review_weight=1.5,  # highest weight for overall judgment
    ),
]


def get_role_for_indicator(indicator_id: str) -> AgentRole:
    """Return the primary expert role for a given indicator."""
    for role in AGENT_ROLES:
        if indicator_id in role.focus_indicators:
            return role
    return AGENT_ROLES[-1]  # chief_reviewer as default


def build_role_prompt(indicator_id: str) -> str:
    """Build a role-specific system prompt preamble for an indicator."""
    role = get_role_for_indicator(indicator_id)
    return (
        f"【当前评审角色】{role.title}\n"
        f"【专业背景】{role.expertise}\n"
        f"【评审要求】请从{role.title}的专业视角出发，"
        f"对该指标进行客观、严格的评审。"
    )


# ═══════════════════════════════════════════════════════════════════════
# Agent Memory — persistent calibration & learning
# ═══════════════════════════════════════════════════════════════════════

_MEMORY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)
_MEMORY_FILE = os.path.join(_MEMORY_DIR, "agent_memory.json")


@dataclass
class IndicatorStats:
    """Running statistics for a single indicator's scores."""
    count: int = 0
    total: float = 0.0
    scores: list[int] = field(default_factory=list)  # last 50 scores
    comment_examples: list[str] = field(default_factory=list)  # last 20


@dataclass
class AgentMemory:
    """Persistent memory for the evaluation agent.

    Tracks scoring patterns across evaluations to:
      - Detect drift (systematic over/under-scoring relative to history)
      - Provide calibration anchors for new evaluations
      - Record reflection-correction trajectories for learning
    """

    total_evaluations: int = 0
    indicator_stats: dict[str, IndicatorStats] = field(default_factory=dict)
    score_corrections: list[dict] = field(default_factory=list)  # correction log
    last_calibration: str = ""  # ISO timestamp of last calibration check

    def record_evaluation(self, secondary_results: list[SecondaryResult]) -> None:
        """Update running statistics after a completed evaluation."""
        self.total_evaluations += 1
        for r in secondary_results:
            if r.id not in self.indicator_stats:
                self.indicator_stats[r.id] = IndicatorStats()
            stats = self.indicator_stats[r.id]
            stats.count += 1
            stats.total += r.score
            stats.scores.append(r.score)
            if len(stats.scores) > 50:
                stats.scores.pop(0)
            if r.comment and len(stats.comment_examples) < 20:
                stats.comment_examples.append(r.comment)

    def record_correction(
        self,
        indicator_id: str,
        original_score: int,
        corrected_score: int,
        reason: str,
    ) -> None:
        """Log a reflection-driven score correction."""
        self.score_corrections.append({
            "indicator_id": indicator_id,
            "original_score": original_score,
            "corrected_score": corrected_score,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        })
        if len(self.score_corrections) > 100:
            self.score_corrections.pop(0)

    def get_calibration_context(self, indicator_id: str) -> str:
        """Build a calibration hint string from historical data.

        Returns empty string if insufficient history (< 3 evaluations).
        """
        stats = self.indicator_stats.get(indicator_id)
        if not stats or stats.count < 3:
            return ""

        mean = stats.total / stats.count
        recent_scores = stats.scores[-10:] if len(stats.scores) >= 10 else stats.scores
        recent_mean = sum(recent_scores) / len(recent_scores)

        return (
            f"【历史校准参考】该指标历史{stats.count}次评分的均值为{mean:.1f}分，"
            f"最近{len(recent_scores)}次均值{recent_mean:.1f}分。"
            f"请以此为参考校准评分尺度，但仍以文献实际质量为准。"
        )

    def detect_score_drift(self, indicator_id: str, new_score: int) -> Optional[str]:
        """Detect if a new score deviates significantly from historical pattern.

        Returns a warning string if the score is >2 std devs from mean,
        or None if within normal range or insufficient history.
        """
        stats = self.indicator_stats.get(indicator_id)
        if not stats or stats.count < 5:
            return None

        mean = stats.total / stats.count
        if stats.count < 2:
            return None

        variance = sum((s - mean) ** 2 for s in stats.scores) / stats.count
        std = variance ** 0.5

        if std < 0.5:  # skip when distribution is very tight
            return None

        deviations = abs(new_score - mean) / std
        if deviations > 2.0:
            direction = "偏高" if new_score > mean else "偏低"
            return (
                f"⚠ 指标 {indicator_id} 得分 {new_score} 显著{direction}"
                f"（历史均值 {mean:.1f}±{std:.1f}，偏离 {deviations:.1f}σ），"
                f"建议复核确认"
            )
        return None

    def save(self) -> None:
        """Persist memory to disk."""
        os.makedirs(_MEMORY_DIR, exist_ok=True)
        data: dict = {
            "total_evaluations": self.total_evaluations,
            "indicator_stats": {
                k: {
                    "count": v.count,
                    "total": v.total,
                    "scores": v.scores,
                    "comment_examples": v.comment_examples,
                }
                for k, v in self.indicator_stats.items()
            },
            "score_corrections": self.score_corrections,
            "last_calibration": self.last_calibration,
        }
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "AgentMemory":
        """Load memory from disk, or return fresh memory."""
        if not os.path.exists(_MEMORY_FILE):
            return cls()
        try:
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            mem = cls(
                total_evaluations=data.get("total_evaluations", 0),
                score_corrections=data.get("score_corrections", []),
                last_calibration=data.get("last_calibration", ""),
            )
            for k, v in data.get("indicator_stats", {}).items():
                mem.indicator_stats[k] = IndicatorStats(
                    count=v["count"],
                    total=v["total"],
                    scores=v.get("scores", []),
                    comment_examples=v.get("comment_examples", []),
                )
            return mem
        except Exception:
            logger.exception("Failed to load agent memory, starting fresh")
            return cls()


# Global memory instance (loaded lazily)
_memory: AgentMemory | None = None


def get_memory() -> AgentMemory:
    """Return the global agent memory, loading if necessary."""
    global _memory
    if _memory is None:
        _memory = AgentMemory.load()
        logger.info(
            "Agent memory loaded: %d evaluations, %d indicators tracked",
            _memory.total_evaluations,
            len(_memory.indicator_stats),
        )
    return _memory


# ═══════════════════════════════════════════════════════════════════════
# Reflection & Self-Correction System
# ═══════════════════════════════════════════════════════════════════════

_REFLECTION_SYSTEM_PROMPT = """你是一位经验丰富的评审委员会主席，现在需要对刚才完成的各二级指标评分进行一致性复核。

【复核任务】
1. 检查各指标评分是否与证据摘要一致（评分不能偏离证据质量）
2. 检查同一一级指标下的二级指标评分比例是否合理
3. 检查是否存在"评分漂移"——开头过严、结尾过松（或反之）
4. 检查满分指标是否有充分证据支撑，零分指标是否确实缺乏依据

【修正原则】
- 只有在明确发现评分偏差时才修正，不要为了"调整"而调整
- 修正幅度一般不超过±2分
- 修正时必须给出具体理由"""


def build_reflection_prompt(
    secondary_results: list[SecondaryResult],
    doc_complexity: dict,
) -> str:
    """Build a meta-evaluation prompt for the reflection loop.

    Args:
        secondary_results: All 16 secondary indicator results from initial pass.
        doc_complexity: Document complexity assessment dict.

    Returns:
        A reflection prompt for consistency checking.
    """
    # Summarize current scores
    score_lines: list[str] = []
    for r in secondary_results:
        primary_id = SECONDARY_TO_PRIMARY.get(r.id, "?")
        score_lines.append(
            f"| {r.id} | {r.name} | {r.score}/{r.max_score} | "
            f"一级{primary_id} | {r.comment} |"
        )

    score_table = "\n".join(score_lines)

    return f"""{_REFLECTION_SYSTEM_PROMPT}

## 当前评分总览
| 编号 | 指标名 | 得分 | 所属一级 | 评语 |
|------|--------|------|----------|------|
{score_table}

## 文献特征
- 类型推断：{doc_complexity.get('estimated_type', '未知')}
- 复杂度：{doc_complexity.get('complexity_level', '未知')}
- 文本长度：{doc_complexity.get('length', 0)} 字符

## 输出要求
请输出复核结果，严格按以下JSON格式：

```json
{{
    "is_consistent": true,
    "corrections": [
        {{
            "id": "3.2",
            "original_score": 4,
            "corrected_score": 3,
            "reason": "证据显示仅有定性论述，对照评分细则的'良好3-4分'标准，应降至3分"
        }}
    ],
    "overall_assessment": "<100字以内的一致性评估>"
}}
```

如果评分一致无需修正，corrections 数组为空即可。"""


def parse_reflection_response(raw: str) -> dict:
    """Parse the reflection model response into structured corrections."""
    from backend.utils.response_parser import parse_llm_response

    parsed = parse_llm_response(raw)
    corrections = parsed.get("corrections", [])
    if isinstance(corrections, str):
        try:
            corrections = json.loads(corrections)
        except (json.JSONDecodeError, TypeError):
            corrections = []
    return {
        "is_consistent": parsed.get("is_consistent", True),
        "corrections": corrections if isinstance(corrections, list) else [],
        "overall_assessment": str(parsed.get("overall_assessment", "")),
    }


# ═══════════════════════════════════════════════════════════════════════
# Core evaluation functions
# ═══════════════════════════════════════════════════════════════════════

def evaluate_secondary_indicator(
    text: str,
    indicator: dict,
    max_chars: int = 6000,
    use_calibration: bool = True,
) -> SecondaryResult:
    """Evaluate one secondary indicator.

    Incorporates:
      - Agent role assignment based on indicator specialty
      - Memory-based calibration context (if available)
      - Retry with fallback on failure

    Args:
        text: Document text.
        indicator: SecondaryIndicator dict from constants.
        max_chars: Characters per prompt.
        use_calibration: Include historical calibration context.

    Returns:
        SecondaryResult with score, evidence, comment.
    """
    truncated = truncate_text(text, max_chars)
    prompt = build_secondary_prompt(truncated, indicator, max_chars)

    # Inject agent role preamble
    role_prompt = build_role_prompt(indicator["id"])
    # Insert after system prompt, before task description
    prompt = prompt.replace(
        "## 评价任务",
        f"{role_prompt}\n\n## 评价任务",
    )

    # Inject calibration context from memory
    if use_calibration:
        calib = get_memory().get_calibration_context(indicator["id"])
        if calib:
            prompt = prompt.replace(
                "## 评价任务",
                f"{calib}\n\n## 评价任务",
            )

    # Inference with retry
    try:
        raw = call_model_with_retry(prompt)
        parsed = parse_llm_response(raw)
    except Exception as exc:
        logger.exception("Indicator %s inference failed after retries", indicator["id"])
        return SecondaryResult(
            id=indicator["id"],
            name=indicator["name"],
            max_score=indicator["max_score"],
            score=0,
            evidence=f"推理失败: {exc}",
            comment="评估失败（已重试）",
        )

    score = min(max(parsed["score"], 0), indicator["max_score"])

    # Check for scoring drift vs memory
    drift_warning = get_memory().detect_score_drift(indicator["id"], score)
    if drift_warning:
        logger.info(drift_warning)

    return SecondaryResult(
        id=indicator["id"],
        name=indicator["name"],
        max_score=indicator["max_score"],
        score=score,
        evidence=str(parsed.get("evidence", ""))[:500],
        comment=str(parsed.get("comment", ""))[:200],
    )


def _parse_bonus_json(raw: str) -> dict:
    """Parse the multi-bonus JSON response."""
    import json as _json
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if m:
        return _json.loads(m.group(1))
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return _json.loads(m.group(0))
    return _json.loads(raw)


def evaluate_additional_items(
    text: str,
    max_chars: int = 6000,
) -> list[AdditionalResult]:
    """Evaluate all additional bonus items in a single LLM call."""
    truncated = truncate_text(text, max_chars)
    prompt = build_additional_prompt(truncated, max_chars)
    results: list[AdditionalResult] = []

    try:
        raw = call_model_with_retry(prompt)
        parsed = _parse_bonus_json(raw)
    except Exception as exc:
        logger.exception("Additional items failed")
        for item in ADDITIONAL_ITEMS:
            results.append(AdditionalResult(
                name=item["name"], score=0, comment=f"评估出错: {exc}",
            ))
        return results

    bonus_map = {
        "bonus_discipline": ("discipline_score", "discipline_comment"),
        "bonus_methodology": ("methodology_score", "methodology_comment"),
        "bonus_timeliness": ("timeliness_score", "timeliness_comment"),
        "bonus_charts": ("chart_score", "chart_comment"),
    }
    for item in ADDITIONAL_ITEMS:
        score_key, comment_key = bonus_map.get(item["id"], ("score", "comment"))
        raw_score = int(parsed.get(score_key, 0))
        lo, hi = item["range"]
        score = max(lo, min(hi, raw_score))
        results.append(AdditionalResult(
            name=item["name"],
            score=score,
            comment=str(parsed.get(comment_key, ""))[:200],
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════
# Reflection loop — consistency check & auto-correction
# ═══════════════════════════════════════════════════════════════════════

def reflect_and_refine(
    secondary_results: list[SecondaryResult],
    text: str,
    doc_complexity: dict,
    enable_reflection: bool = True,
) -> tuple[list[SecondaryResult], list[dict]]:
    """Run the reflection loop: review score consistency and apply corrections.

    Args:
        secondary_results: All 16 indicator results from initial pass.
        text: Document text (for context).
        doc_complexity: Document complexity assessment.
        enable_reflection: If False, skip and return unchanged.

    Returns:
        Tuple of (possibly corrected results, correction log entries).
    """
    if not enable_reflection:
        return secondary_results, []

    logger.info("Starting reflection loop for consistency check")

    # Build reflection prompt
    reflection_prompt = build_reflection_prompt(secondary_results, doc_complexity)

    try:
        raw = call_model(reflection_prompt, max_new_tokens=512, temperature=0.1)
    except Exception:
        logger.exception("Reflection inference failed, skipping correction")
        return secondary_results, []

    reflection = parse_reflection_response(raw)
    corrections = reflection.get("corrections", [])

    if not corrections:
        logger.info("Reflection: no corrections needed — scores are consistent")
        return secondary_results, []

    # Apply corrections
    result_map = {r.id: r for r in secondary_results}
    correction_log: list[dict] = []

    for corr in corrections:
        indicator_id = corr.get("id", "")
        corrected_score = corr.get("corrected_score")
        reason = corr.get("reason", "")

        if not indicator_id or corrected_score is None:
            continue

        original = result_map.get(indicator_id)
        if original is None:
            continue

        old_score = original.score
        if old_score == corrected_score:
            continue

        # Clamp to indicator's max score
        corrected_score = max(0, min(corrected_score, original.max_score))

        logger.info(
            "Reflection correction: %s %d → %d (%s)",
            indicator_id, old_score, corrected_score, reason,
        )

        # Log correction
        get_memory().record_correction(indicator_id, old_score, corrected_score, reason)
        correction_log.append({
            "indicator_id": indicator_id,
            "original_score": old_score,
            "corrected_score": corrected_score,
            "reason": reason,
        })

        # Update result
        result_map[indicator_id] = SecondaryResult(
            id=original.id,
            name=original.name,
            max_score=original.max_score,
            score=corrected_score,
            evidence=original.evidence,
            comment=f"{original.comment} [已复核校正]",
        )

    logger.info(
        "Reflection complete: %d/%d indicators corrected",
        len(correction_log), len(secondary_results),
    )

    return [result_map[r.id] for r in secondary_results], correction_log


# ═══════════════════════════════════════════════════════════════════════
# Full document evaluation — pipeline orchestrator
# ═══════════════════════════════════════════════════════════════════════

def evaluate_document(
    text: str,
    doc_name: str = "未命名文献",
    max_chars: int = 12000,
    include_additional: bool = True,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    enable_reflection: bool = True,
    enable_memory: bool = True,
) -> EvaluationResponse:
    """Full document evaluation — the main pipeline entry point.

    Pipeline stages:
      1. Assess document complexity
      2. Evaluate all 16 secondary indicators (sequential, with memory)
      3. Reflection loop — consistency check & auto-correction
      4. Aggregate by primary indicator
      5. Evaluate additional item
      6. Update persistent memory

    Args:
        text: Full document text.
        doc_name: Filename for display.
        max_chars: Characters per indicator prompt.
        include_additional: Whether to evaluate the ±5 additional item.
        progress_callback: Optional fn(current: int, total: int).
        enable_reflection: Whether to run the self-reflection loop.
        enable_memory: Whether to use persistent calibration memory.

    Returns:
        EvaluationResponse with all results aggregated.
    """
    start_time = time.time()
    memory = get_memory() if enable_memory else None
    correction_log: list[dict] = []

    # ── Stage 0: Document assessment ─────────────────────────────────
    doc_complexity = assess_document_complexity(text)
    logger.info(
        "Evaluating '%s' — type: %s, complexity: %s, length: %d",
        doc_name,
        doc_complexity["estimated_type"],
        doc_complexity["complexity_level"],
        doc_complexity["length"],
    )

    # ── Stage 0.5: AI-based detection of applicable optional indicators
    detection = detect_applicable_indicators(text)
    has_data = detection["has_data"]
    has_policy = detection["has_policy"]
    has_foresight = detection["has_foresight"]
    active_secondary = get_active_secondary_indicators(has_data, has_policy, has_foresight)
    active_primary = get_active_primary_indicators(has_data, has_policy, has_foresight)
    excluded_ids = get_excluded_indicator_ids(has_data, has_policy, has_foresight)
    included_max = float(sum(s["max_score"] for s in active_secondary))
    scale_factor = 100.0 / included_max if included_max > 0 else 1.0
    logger.info(
        "AI detection: data=%s policy=%s foresight=%s — "
        "%d secondary / %d primary, excluded=%s, scale=%.3f",
        has_data, has_policy, has_foresight,
        len(active_secondary), len(active_primary),
        excluded_ids, scale_factor,
    )

    # ── Stage 1+2: Evaluate secondary indicators (raw scores) ────────
    all_secondary: list[SecondaryResult] = []
    total = len(active_secondary)

    for i, indicator in enumerate(active_secondary):
        try:
            result = evaluate_secondary_indicator(
                text, indicator, max_chars,
                use_calibration=enable_memory,
            )
        except Exception as exc:
            logger.exception("Indicator %s failed completely", indicator["id"])
            result = SecondaryResult(
                id=indicator["id"],
                name=indicator["name"],
                max_score=indicator["max_score"],
                score=0,
                evidence=f"评估出错: {exc}",
                comment="评估失败",
            )
        all_secondary.append(result)

        if progress_callback:
            progress_callback(i + 1, total)

    # ── Stage 3: Reflection loop ─────────────────────────────────────
    if enable_reflection:
        all_secondary, correction_log = reflect_and_refine(
            all_secondary, text, doc_complexity,
            enable_reflection=enable_reflection,
        )

    # ── Stage 4: Scale scores to 100-point system & aggregate ───────
    # Scale each secondary score proportionally so the total = 100
    scaled_secondary: dict[str, SecondaryResult] = {}
    for r in all_secondary:
        scaled_score = min(round(r.score * scale_factor), r.max_score)
        scaled_secondary[r.id] = SecondaryResult(
            id=r.id,
            name=r.name,
            max_score=r.max_score,  # keep original max_score for display
            score=scaled_score,
            evidence=r.evidence,
            comment=r.comment,
        )

    primary_results: list[PrimaryResult] = []
    for primary in active_primary:
        secondary_results = [
            scaled_secondary[s["id"]]
            for s in primary["secondary"]
            if s["id"] in scaled_secondary
        ]
        primary_score = sum(r.score for r in secondary_results)
        primary_results.append(PrimaryResult(
            id=primary["id"],
            name=primary["name"],
            weight=primary["weight"],
            score=primary_score,
            secondary_results=secondary_results,
        ))

    # Add placeholder entries for excluded secondaries under their parent primary
    excluded_set = set(excluded_ids)
    for primary in PRIMARY_INDICATORS:
        excluded_subs = [s for s in primary["secondary"] if s["id"] in excluded_set]
        if not excluded_subs:
            continue
        existing = next((p for p in primary_results if p.id == primary["id"]), None)
        if existing:
            # Append excluded secondaries as placeholders
            for s in excluded_subs:
                existing.secondary_results.append(SecondaryResult(
                    id=s["id"],
                    name=s["name"],
                    max_score=s["max_score"],
                    score=0,
                    evidence="该文献不含对应内容，此项不适用",
                    comment="不适用（已跳过）",
                ))
        else:
            # Entire primary was excluded (shouldn't happen with current groups)
            placeholder_secondaries = [
                SecondaryResult(
                    id=s["id"],
                    name=s["name"],
                    max_score=s["max_score"],
                    score=0,
                    evidence="该文献不含对应内容，此项不适用",
                    comment="不适用（已跳过）",
                )
                for s in excluded_subs
            ]
            primary_results.append(PrimaryResult(
                id=primary["id"],
                name=primary["name"],
                weight=primary["weight"],
                score=0,
                secondary_results=placeholder_secondaries,
            ))

    base_score = sum(p.score for p in primary_results)

    # ── Stage 5: Additional bonus items ───────────────────────────────
    additional_results: list[AdditionalResult] = []
    total_score = float(base_score)
    if include_additional:
        try:
            additional_results = evaluate_additional_items(text, max_chars)
        except Exception as exc:
            logger.exception("Additional items failed")
            for item in ADDITIONAL_ITEMS:
                additional_results.append(AdditionalResult(
                    name=item["name"], score=0, comment=f"评估出错: {exc}",
                ))
        total_score = float(base_score + sum(a.score for a in additional_results))

    overall_comment = _classify_overall(total_score)

    # ── Stage 6: Update persistent memory ────────────────────────────
    if enable_memory and memory is not None:
        try:
            memory.record_evaluation(all_secondary)
            memory.last_calibration = datetime.now().isoformat()
            memory.save()
        except Exception:
            logger.exception("Failed to save agent memory")

    elapsed = time.time() - start_time
    bonus_sum = sum(a.score for a in additional_results)
    logger.info(
        "Evaluation complete: %s — %.1f/100 (base %.1f + bonus %+d, %.1fs, %d corrections)",
        doc_name, total_score, base_score, bonus_sum, elapsed, len(correction_log),
    )

    return EvaluationResponse(
        id=datetime.now().strftime("%Y%m%d%H%M%S%f"),
        doc_name=doc_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_score=round(total_score, 1),
        base_score=float(base_score),
        scale_factor=scale_factor,
        excluded_indicators=excluded_ids,
        primary_results=primary_results,
        additional_results=additional_results,
        overall_comment=overall_comment,
    )


# ═══════════════════════════════════════════════════════════════════════
# Async evaluation — parallel indicator execution
# ═══════════════════════════════════════════════════════════════════════

async def evaluate_document_async(
    text: str,
    doc_name: str = "未命名文献",
    max_chars: int = 12000,
    include_additional: bool = True,
    enable_reflection: bool = True,
    enable_memory: bool = True,
    max_concurrency: int = 3,
) -> EvaluationResponse:
    """Async version — evaluates indicators in parallel with concurrency limit.

    Use this in FastAPI endpoints to avoid blocking the event loop.
    Sequential (evaluate_document) is preferred for local/streamlit use
    because parallel GPU inference can cause VRAM contention.
    """
    start_time = time.time()
    memory = get_memory() if enable_memory else None

    doc_complexity = assess_document_complexity(text)
    logger.info(
        "Async evaluating '%s' — type: %s, complexity: %s",
        doc_name,
        doc_complexity["estimated_type"],
        doc_complexity["complexity_level"],
    )

    # AI-based detection of applicable optional indicators
    detection = detect_applicable_indicators(text)
    has_data = detection["has_data"]
    has_policy = detection["has_policy"]
    has_foresight = detection["has_foresight"]
    active_secondary = get_active_secondary_indicators(has_data, has_policy, has_foresight)
    active_primary = get_active_primary_indicators(has_data, has_policy, has_foresight)
    excluded_ids = get_excluded_indicator_ids(has_data, has_policy, has_foresight)
    included_max = float(sum(s["max_score"] for s in active_secondary))
    scale_factor = 100.0 / included_max if included_max > 0 else 1.0
    logger.info(
        "Async AI detection: data=%s policy=%s foresight=%s, "
        "%d indicators, excluded=%s, scale=%.3f",
        has_data, has_policy, has_foresight,
        len(active_secondary), excluded_ids, scale_factor,
    )

    # Prepare all prompts first (Stage 1)
    prompts: list[tuple[SecondaryIndicator, str]] = []
    for indicator in active_secondary:
        truncated = truncate_text(text, max_chars)
        prompt = build_secondary_prompt(truncated, indicator, max_chars)
        role_prompt = build_role_prompt(indicator["id"])
        prompt = prompt.replace("## 评价任务", f"{role_prompt}\n\n## 评价任务")
        if enable_memory:
            calib = get_memory().get_calibration_context(indicator["id"])
            if calib:
                prompt = prompt.replace("## 评价任务", f"{calib}\n\n## 评价任务")
        prompts.append((indicator, prompt))

    # Run all prompts in parallel (Stage 2)
    prompt_texts = [p for _, p in prompts]
    raw_responses = await batch_infer_async(
        prompt_texts,
        max_concurrency=max_concurrency,
    )

    # Parse all responses (Stage 3)
    all_secondary: list[SecondaryResult] = []
    for (indicator, _), raw in zip(prompts, raw_responses):
        try:
            parsed = parse_llm_response(raw)
            score = min(max(parsed["score"], 0), indicator["max_score"])
            all_secondary.append(SecondaryResult(
                id=indicator["id"],
                name=indicator["name"],
                max_score=indicator["max_score"],
                score=score,
                evidence=str(parsed.get("evidence", ""))[:500],
                comment=str(parsed.get("comment", ""))[:200],
            ))
        except Exception as exc:
            logger.exception("Async: indicator %s failed", indicator["id"])
            all_secondary.append(SecondaryResult(
                id=indicator["id"],
                name=indicator["name"],
                max_score=indicator["max_score"],
                score=0,
                evidence=f"推理失败: {exc}",
                comment="评估失败",
            ))

    # Reflection loop
    correction_log: list[dict] = []
    if enable_reflection:
        all_secondary, correction_log = reflect_and_refine(
            all_secondary, text, doc_complexity,
            enable_reflection=enable_reflection,
        )

    # Scale scores to 100-point system
    scaled_secondary: dict[str, SecondaryResult] = {}
    for r in all_secondary:
        scaled_score = min(round(r.score * scale_factor), r.max_score)
        scaled_secondary[r.id] = SecondaryResult(
            id=r.id,
            name=r.name,
            max_score=r.max_score,
            score=scaled_score,
            evidence=r.evidence,
            comment=r.comment,
        )

    # Aggregate — build primary results from scaled secondaries
    primary_results: list[PrimaryResult] = []
    for primary in active_primary:
        secondary_results = [
            scaled_secondary[s["id"]]
            for s in primary["secondary"]
            if s["id"] in scaled_secondary
        ]
        primary_score = sum(r.score for r in secondary_results)
        primary_results.append(PrimaryResult(
            id=primary["id"],
            name=primary["name"],
            weight=primary["weight"],
            score=primary_score,
            secondary_results=secondary_results,
        ))

    # Add placeholder entries for excluded secondaries
    excluded_set = set(excluded_ids)
    for primary in PRIMARY_INDICATORS:
        excluded_subs = [s for s in primary["secondary"] if s["id"] in excluded_set]
        if not excluded_subs:
            continue
        existing = next((p for p in primary_results if p.id == primary["id"]), None)
        if existing:
            for s in excluded_subs:
                existing.secondary_results.append(SecondaryResult(
                    id=s["id"],
                    name=s["name"],
                    max_score=s["max_score"],
                    score=0,
                    evidence="该文献不含对应内容，此项不适用",
                    comment="不适用（已跳过）",
                ))
        else:
            primary_results.append(PrimaryResult(
                id=primary["id"],
                name=primary["name"],
                weight=primary["weight"],
                score=0,
                secondary_results=[
                    SecondaryResult(
                        id=s["id"],
                        name=s["name"],
                        max_score=s["max_score"],
                        score=0,
                        evidence="该文献不含对应内容，此项不适用",
                        comment="不适用（已跳过）",
                    )
                    for s in excluded_subs
                ],
            ))

    base_score = sum(p.score for p in primary_results)
    total_score = float(base_score)

    additional_results: list[AdditionalResult] = []
    if include_additional:
        try:
            additional_results = evaluate_additional_items(text, max_chars)
        except Exception as exc:
            logger.exception("Async: additional items failed")
            for item in ADDITIONAL_ITEMS:
                additional_results.append(AdditionalResult(
                    name=item["name"], score=0, comment=f"评估出错: {exc}",
                ))
        total_score = float(base_score + sum(a.score for a in additional_results))

    overall_comment = _classify_overall(total_score)

    # Update memory
    if enable_memory and memory is not None:
        try:
            memory.record_evaluation(all_secondary)
            memory.last_calibration = datetime.now().isoformat()
            memory.save()
        except Exception:
            logger.exception("Failed to save agent memory")

    elapsed = time.time() - start_time
    logger.info(
        "Async evaluation complete: %s — %.1f/100 (%.1fs, %d corrections)",
        doc_name, total_score, elapsed, len(correction_log),
    )

    return EvaluationResponse(
        id=datetime.now().strftime("%Y%m%d%H%M%S%f"),
        doc_name=doc_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_score=round(total_score, 1),
        base_score=float(base_score),
        scale_factor=scale_factor,
        excluded_indicators=excluded_ids,
        primary_results=primary_results,
        additional_results=additional_results,
        overall_comment=overall_comment,
    )


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _classify_overall(score: float) -> str:
    """Classify total score against 100-point scale thresholds."""
    for threshold, comment in OVERALL_THRESHOLDS:
        if score >= threshold:
            return comment
    return OVERALL_THRESHOLDS[-1][1]


def get_memory_stats() -> dict:
    """Return summary statistics from agent memory (for monitoring)."""
    mem = get_memory()
    indicator_summaries: dict[str, dict] = {}
    for ind_id, stats in mem.indicator_stats.items():
        if stats.count > 0:
            mean = stats.total / stats.count
            recent = stats.scores[-10:] if len(stats.scores) >= 10 else stats.scores
            indicator_summaries[ind_id] = {
                "count": stats.count,
                "mean": round(mean, 2),
                "recent_mean": round(sum(recent) / len(recent), 2) if recent else 0,
            }
    return {
        "total_evaluations": mem.total_evaluations,
        "total_corrections": len(mem.score_corrections),
        "indicators_tracked": len(mem.indicator_stats),
        "indicator_summaries": indicator_summaries,
        "last_calibration": mem.last_calibration,
    }
