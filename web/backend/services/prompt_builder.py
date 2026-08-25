"""Stage 1 — Prompt Construction for the Intelligent Evaluation Agent.

Three-stage pipeline:
  1. Prompt Construction (this module) → builds structured evaluation prompts
  2. Model Inference (llm_client)        → runs LLM with retry/validation
  3. Response Parsing (evaluation_service) → orchestrates + aggregates

Key design decisions:
  - Chain-of-thought reasoning framework baked into every prompt
  - Few-shot calibration examples to normalize scoring across calls
  - Smart keyword-guided truncation that extracts relevant passages
  - Structured JSON schema enforcement in output instructions
  - Adaptive prompt intensity based on document complexity signals
"""

from __future__ import annotations

import re
from typing import Optional

from shared.constants import (
    PRIMARY_INDICATORS,
    ALL_SECONDARY_INDICATORS,
    ADDITIONAL_ITEMS,
    SecondaryIndicator,
    PrimaryIndicator,
    get_primary_by_id,
    SECONDARY_TO_PRIMARY,
)

# ═══════════════════════════════════════════════════════════════════════
# Agent Persona — the system-level role definition
# ═══════════════════════════════════════════════════════════════════════

_AGENT_SYSTEM_PROMPT = """你是一位资深的中医药政策研究评审专家，身份定位如下：

【核心能力】
- 10年以上中医药政策研究与评审经验，熟悉《中医药法》《中医药发展战略规划纲要》等政策体系
- 精通政策文献质量评估方法论，能准确识别研究设计、数据来源、论证逻辑等维度的优劣
- 具备量化评分与质性评价双重能力，评分稳定、标准一致

【评分尺度校准 — 重要】
- 当前评审对象为已发表或通过答辩的学术研究论文/政策研究报告，整体质量已达到基本学术标准
- 「良好」应作为评分基准——只要文献在该维度上内容充实、论述清晰，即应从良好档位起评
- 仅当文献在某维度存在明显缺陷（如完全缺失关键要素、逻辑混乱、数据不可信）时才降至「一般」或「差」
- 「优秀」用于该维度表现突出、超出同类文献平均水平的情况
- 评分时请充分使用分数区间的中上段，避免系统性偏低

【评审原则】
1. 客观公正：严格依据评分标准，不受文献作者、机构、发表平台等外在因素影响
2. 证据导向：每个评分必须有文献原文证据支撑，不可凭空推测
3. 标准一致：对同一指标的不同文献采用完全相同的评分尺度
4. 独立判断：每个指标独立评分，不受其他指标得分影响

【输出规范】
- 严格遵循JSON格式输出，不得添加任何多余文字
- 评分必须在指定区间内取整数
- 证据摘录需直接来自文献原文，100-200字
- 评语需简洁精准，50字以内，注明等级"""


# ═══════════════════════════════════════════════════════════════════════
# Chain-of-Thought Reasoning Framework
# ═══════════════════════════════════════════════════════════════════════

_COT_FRAMEWORK = """【推理步骤】
请按以下步骤逐项分析后再给出评分：

Step 1 — 信息定位：在文献中找到与该指标相关的所有段落/数据
Step 2 — 标准对照：逐条对照评价标准中的每一项要求，判断文献满足程度
Step 3 — 等级判定：根据评分细则确定所属等级（优秀/良好/一般/差）
Step 4 — 档内细调：在等级对应的分数区间内，根据完成质量精细调整
Step 5 — 证据锚定：从文献中摘录最有力的支撑原文作为评分依据

请先在内心完成以上推理，然后仅输出JSON结果。"""


# ═══════════════════════════════════════════════════════════════════════
# Few-Shot Calibration Examples
# ═══════════════════════════════════════════════════════════════════════

_FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "indicator_id": "1.1",
        "indicator_name": "背景描述",
        "max_score": 8,
        "example_output": {
            "score": 6,
            "evidence": "本文系统梳理了2019-2022年中医药医保支付改革的政策演变，引用国家医保局统计数据说明中药饮片报销比例从45%提升至68%，并结合浙江省DRG付费改革案例分析了政策出台的经济动因。",
            "comment": "良好：梳理清晰有数据但近3年关键节点分析稍显薄弱",
        },
    },
    {
        "indicator_id": "2.1",
        "indicator_name": "方法论",
        "max_score": 8,
        "example_output": {
            "score": 7,
            "evidence": "研究采用混合方法设计，定量部分使用双重差分模型评估政策效果（数据来源：2018-2021年全国中医医院统计年报），定性部分对12位省级中医药管理局负责人进行半结构化访谈，并在讨论部分详细说明了DID模型平行趋势假设的局限性。",
            "comment": "优秀：方法多元且匹配度高，局限性分析透彻",
        },
    },
    {
        "indicator_id": "3.2",
        "indicator_name": "成本效益",
        "max_score": 6,
        "example_output": {
            "score": 3,
            "evidence": "文中提到'该政策实施后将有效降低基层医疗支出'，但未给出具体测算依据或成本数据，也未对比不同方案的经济性。",
            "comment": "良好偏下：有定性论述但缺乏量化分析支撑",
        },
    },
]


def _get_few_shot_for_indicator(indicator_id: str) -> Optional[dict]:
    """Return a calibration example for the given indicator, if available."""
    for ex in _FEW_SHOT_EXAMPLES:
        if ex["indicator_id"] == indicator_id:
            return ex
    return None


# ═══════════════════════════════════════════════════════════════════════
# Smart Text Preprocessing
# ═══════════════════════════════════════════════════════════════════════

# Keyword groups mapped to each secondary indicator for relevance extraction
_INDICATOR_KEYWORDS: dict[str, list[str]] = {
    "1.1": ["背景", "政策演变", "发展历程", "政策脉络", "历史", "沿革", "现状"],
    "1.2": ["问题", "矛盾", "冲突", "痛点", "挑战", "困境", "利益相关", "核心问题"],
    "2.1": ["方法", "方法论", "研究设计", "定量", "定性", "模型", "实证", "回归", "访谈", "问卷"],
    "2.2": ["数据", "统计", "调查", "样本", "来源", "数据库", "年鉴", "卫健委", "统计局"],
    "2.3": ["分析", "检验", "显著性", "信度", "效度", "软件", "工具", "结果", "发现"],
    "3.1": ["建议", "措施", "对策", "方案", "实施", "执行", "责任", "部门", "分工", "路线图"],
    "3.2": ["成本", "效益", "投入", "产出", "预算", "财政", "经济", "资金", "回报"],
    "3.3": ["风险", "预警", "预案", "不确定性", "防范", "应对", "危机"],
    "4.1": ["结构", "逻辑", "论证", "框架", "层次", "递进", "推理", "章", "节"],
    "4.2": ["引用", "文献", "参考", "证据", "案例", "佐证", "支持", "来源"],
    "5.1": ["创新", "新颖", "原创", "突破", "首创", "新方法", "新理论", "新框架", "融合"],
    "5.2": ["趋势", "前瞻", "预测", "未来", "展望", "趋势", "老龄化", "AI", "人工智能", "数字化"],
    "6.1": ["语言", "表达", "术语", "行文", "摘要", "措辞", "流畅", "清晰"],
    "6.2": ["格式", "规范", "引用", "图表", "编号", "参考文献", "标准"],
    "7.1": ["政策", "影响", "采纳", "决策", "实施", "应用", "落地", "部门", "政府"],
    "7.2": ["社会", "公众", "媒体", "舆论", "民众", "群体", "公平", "参与"],
}


def smart_truncate(
    text: str,
    indicator_id: str,
    max_chars: int = 6000,
    context_margin: int = 200,
) -> str:
    """Truncate document text while preserving sections relevant to the indicator.

    Strategy:
      1. If text fits within max_chars, return as-is.
      2. Score each paragraph by keyword relevance to the indicator.
      3. Keep top-scoring paragraphs up to the char budget, with surrounding context.
      4. Fall back to head+tail sampling if no keyword matches.

    Args:
        text: Full document text.
        indicator_id: Secondary indicator ID (e.g. "1.1", "3.2").
        max_chars: Target maximum character count.
        context_margin: Characters of surrounding context to include per match.

    Returns:
        Truncated text biased toward relevant passages.
    """
    if len(text) <= max_chars:
        return text

    keywords = _INDICATOR_KEYWORDS.get(indicator_id, [])
    if not keywords:
        return text[:max_chars] + "…[内容截断]"

    # Split into paragraphs (double newline or single newline for short paras)
    paragraphs = re.split(r"\n\s*\n", text)
    if len(paragraphs) <= 1:
        # Try sentence-level splitting
        paragraphs = re.split(r"(?<=[。！？\n])", text)

    # Score each paragraph by keyword density
    scored: list[tuple[int, float, str]] = []  # (index, score, para)
    for idx, para in enumerate(paragraphs):
        if not para.strip():
            continue
        para_lower = para.lower()
        hits = sum(1 for kw in keywords if kw.lower() in para_lower)
        if hits > 0:
            # Weight by hit count relative to paragraph length (favor density)
            density = hits / max(len(para) / 100, 1)
            scored.append((idx, density, para))

    if not scored:
        # No keyword matches — keep head and tail
        half = max_chars // 2
        return text[:half] + "\n…[中间内容省略]…\n" + text[-half:]

    # Sort by score descending, select until budget exhausted
    scored.sort(key=lambda x: x[1], reverse=True)
    selected_indices: set[int] = set()
    budget = max_chars
    result_parts: list[str] = []

    for idx, score, para in scored:
        if budget <= 0:
            break
        # Add context paragraphs around the match
        for ctx_idx in range(
            max(0, idx - 1),
            min(len(paragraphs), idx + 2),
        ):
            if ctx_idx in selected_indices:
                continue
            ctx_para = paragraphs[ctx_idx].strip()
            if not ctx_para:
                continue
            if len(ctx_para) > budget + context_margin:
                ctx_para = ctx_para[:budget] + "…"
            if budget - len(ctx_para) < 0:
                ctx_para = ctx_para[:budget] + "…"
            selected_indices.add(ctx_idx)
            result_parts.append(ctx_para)
            budget -= len(ctx_para)
            if budget <= 0:
                break

    if not result_parts:
        return text[:max_chars] + "…[内容截断]"

    return "\n\n".join(result_parts) + ("…[内容截断]" if budget <= 0 else "")


# ═══════════════════════════════════════════════════════════════════════
# Prompt Builders — the three pipeline entry points
# ═══════════════════════════════════════════════════════════════════════

def build_secondary_prompt(
    text: str,
    indicator: SecondaryIndicator,
    max_chars: int = 6000,
    use_cot: bool = True,
    use_few_shot: bool = True,
) -> str:
    """Stage 1 — Build an evaluation prompt for a single secondary indicator.

    The prompt includes:
      - Agent expert persona
      - Chain-of-thought reasoning framework
      - Indicator-specific evaluation criteria + scoring guide
      - Few-shot calibration example (if available)
      - Smart-truncated document text
      - Strict JSON output format instructions

    Args:
        text: Document text (full, will be smart-truncated internally).
        indicator: The SecondaryIndicator dict from constants.
        max_chars: Approximate character budget for the document portion.
        use_cot: Include chain-of-thought reasoning instructions.
        use_few_shot: Include calibration example when available.

    Returns:
        A fully formatted evaluation prompt string.
    """
    truncated = smart_truncate(text, indicator["id"], max_chars)
    primary = get_primary_by_id(SECONDARY_TO_PRIMARY[indicator["id"]])

    # Build the prompt sections
    sections: list[str] = [_AGENT_SYSTEM_PROMPT]

    sections.append(f"""## 评价任务
请对以下文献的二级指标进行专业评分。

**一级指标**：{primary["name"]}（满分 {primary["weight"]} 分）
**二级指标 {indicator["id"]}**：{indicator["name"]}（满分 {indicator["max_score"]} 分）

## 评价标准
{indicator["criteria"]}

## 评分细则
{indicator["scoring_guide"]}

## 关键判断依据
{indicator["evidence_guide"]}""")

    if use_cot:
        sections.append(_COT_FRAMEWORK)

    if use_few_shot:
        example = _get_few_shot_for_indicator(indicator["id"])
        if example:
            import json as _json
            sections.append(f"""## 评分校准示例
以下是一个{example["max_score"]}分制下该指标的评分示例，请以此为基准校准你的评分尺度：

```json
{_json.dumps(example["example_output"], ensure_ascii=False, indent=2)}
```""")

    sections.append(f"""## 文献内容
{truncated}

## 输出要求
严格按以下JSON格式输出（不要添加其他任何内容，不要有多余的解释或前缀）：

```json
{{
    "score": <0—{indicator["max_score"]}的整数>,
    "evidence": "<从文献中直接提取的原文证据或对原文内容的准确概括，100—200字>",
    "comment": "<50字以内的简短评语，必须注明等级（优秀/良好/一般/差）>"
}}
```""")

    return "\n\n".join(sections)


def build_full_evaluation_prompt(
    text: str,
    max_chars: int = 6000,
    use_cot: bool = True,
) -> str:
    """Build a comprehensive prompt for evaluating ALL 16 secondary
    indicators plus the additional item in a single LLM call.

    Use this when the model context window supports it (e.g., >= 32K tokens).

    Returns:
        A comprehensive evaluation prompt covering all indicators.
    """
    # For full evaluation, use head+tail sampling since we need broad coverage
    if len(text) > max_chars:
        half = max_chars // 2
        truncated = text[:half] + "\n…[中间内容省略，保留首尾关键部分]…\n" + text[-half:]
    else:
        truncated = text

    # Build indicator catalog
    indicator_blocks: list[str] = []
    for primary in PRIMARY_INDICATORS:
        for s in primary["secondary"]:
            indicator_blocks.append(
                f"### {s['id']} {s['name']}（满分{s['max_score']}分，"
                f"所属一级指标：{primary['id']} {primary['name']}）\n"
                f"**评价标准**：\n{s['criteria']}\n"
                f"**评分细则**：\n{s['scoring_guide']}\n"
                f"**判断依据**：\n{s['evidence_guide']}\n"
            )

    indicators_section = "\n".join(indicator_blocks)

    cot_section = ""
    if use_cot:
        cot_section = """【全局推理要求】
在评分前，请先在心中完成以下分析：
1. 快速浏览全文，把握文献类型（学术论文/政策报告/调研报告）和主题领域
2. 逐项对照16个指标的评价标准，在文献中定位关键信息
3. 注意指标间的逻辑关联（如方法论评价会影响数据分析评价）
4. 确保各指标评分尺度一致，不出现同一文献中相似质量对应悬殊分数的情况"""

    return f"""{_AGENT_SYSTEM_PROMPT}

## 评价任务
请对以下中医药政策文献进行全面系统评价，覆盖7个一级指标下的16个二级指标。

{cot_section}

## 指标体系
{indicators_section}

## 文献内容
{truncated}

## 输出要求
按以下JSON格式一次性输出所有16个二级指标的评价结果，外加附加项评价。

**输出格式（严格遵循）：**
```json
{{
    "secondary_results": [
        {{
            "id": "1.1",
            "score": <0—8的整数>,
            "evidence": "<证据摘要，100—200字>",
            "comment": "<50字以内，标注等级>"
        }},
        {{
            "id": "1.2",
            "score": <0—7的整数>,
            "evidence": "<证据摘要>",
            "comment": "<评语>"
        }}
    ],
    "additional": {{
        "score": <±5的整数>,
        "comment": "<30字以内评语>"
    }},
    "overall_comment": "<200字以内的综合评价，概括文献的核心优势与改进方向>"
}}
```

**重要提醒：**
- secondary_results 数组必须包含全部16个二级指标（id: 1.1—7.2），不可遗漏
- 所有 score 必须是整数，不得出现小数
- evidence 必须引用文献内容，不可凭空编造
- 评分尺度请保持一致：同一等级的标准适用于所有指标"""


def build_additional_prompt(
    text: str,
    max_chars: int = 6000,
) -> str:
    """Build a prompt that evaluates all additional bonus items in one call."""
    truncated = smart_truncate(text, "1.1", max_chars)

    items_desc = []
    for item in ADDITIONAL_ITEMS:
        items_desc.append(
            f"- **{item['name']}**（{item['range'][0]}到+{item['range'][1]}分）：{item['description']}"
        )

    return f"""{_AGENT_SYSTEM_PROMPT}

## 评价任务
请对文献进行以下四项附加评分。

## 附加评分项
{chr(10).join(items_desc)}

## 评分指南

**学科适配性**（0到+5）：
- +3到+5：紧密围绕中医药政策核心议题，体现鲜明的中医药学科特色
- +1到+2：涉及中医药政策但部分偏向通用公共政策讨论
- 0：一般性政策研究，无明显学科偏向或不涉及中医药主题

**方法学复杂度**（0到+3）：
- +3：使用了DID、ITS、多元回归、决策树/随机森林、DEA、PSM、SEM等进阶方法且适用得当
- +2：使用了较复杂的统计分析方法（如回归分析、相关性检验）
- +1：使用了基础统计方法（描述性统计、t检验等）
- 0：纯定性分析或无明确方法论

**政策时效性**（0到+2）：
- +2：文献议题属于当前改革热点（如DRG/DIP付费改革、中医药振兴重大工程、智慧共享中药房、紧密型医联体、医保支付方式改革、公立医院高质量发展等），具有显著时效性溢价
- +1：议题有一定现实意义但非核心热点
- 0：历史回顾性研究或议题已过政策窗口期

**图表质量**（0到+5）：
- +4到+5：文献包含规范、清晰、信息丰富的表格和/或图表（如数据可视化、统计图、流程图），能有效支撑论证，格式标准
- +2到+3：有表格或图表但不够规范（如缺少标题/编号、排版欠佳），或信息量有限
- +1：仅有简单表格，图表较少或质量一般
- 0：文献无表格或图表

## 文献内容
{truncated}

## 输出要求
严格按以下JSON格式输出：
```json
{{
    "discipline_score": <0到5的整数>,
    "discipline_comment": "<30字以内>",
    "methodology_score": <0到3的整数>,
    "methodology_comment": "<30字以内>",
    "timeliness_score": <0到2的整数>,
    "timeliness_comment": "<30字以内>",
    "chart_score": <0到5的整数>,
    "chart_comment": "<30字以内>"
}}
```"""


# ═══════════════════════════════════════════════════════════════════════
# Optional Indicator Detection — AI-based pre-scan
# ═══════════════════════════════════════════════════════════════════════

_OPTIONAL_DETECTION_PROMPT = """你是一位文献分析专家。请阅读以下文献内容，判断该文献是否包含以下三类内容。

【判断标准】

1. **数据内容**（决定是否纳入"数据来源""数据分析"两项二级指标）：
   - 文献是否包含统计数据、调查样本、量化分析、图表等实质性数据内容？
   - 仅罗列政策条文或纯文字描述不算。需要具体的数据来源、样本量、统计结果等。

2. **政策建议**（决定是否纳入"可操作性""成本效益""风险评估"三项二级指标）：
   - 文献是否提出了具体的政策建议、改革方案或改进措施？
   - 仅提及"需要进一步研究""应加强管理"等笼统表述不算。
   - 需要包含具体的实施路径、责任主体、资源配置方案或风险评估等。

3. **前瞻内容**（决定是否纳入"前瞻性"一项二级指标）：
   - 文献是否包含对未来趋势的预测、技术展望或中长期战略规划？
   - 需要涉及AI/数字化/老龄化等趋势对领域的影响分析，或弹性政策机制设计等。

【输出格式】
严格按以下JSON格式输出，不得添加任何其他内容：
{
    "has_data": true/false,
    "has_policy": true/false,
    "has_foresight": true/false,
    "reason_data": "<30字以内，说明判断依据>",
    "reason_policy": "<30字以内>",
    "reason_foresight": "<30字以内>"
}

【文献内容】
"""


def detect_applicable_indicators(text: str, max_chars: int = 4000) -> dict:
    """AI-based pre-scan to determine which optional indicator groups apply.

    Makes a single LLM call to analyze the document semantically and decide
    whether data, policy, and foresight content is present.

    Args:
        text: Document text (will be truncated to max_chars for the scan).
        max_chars: Characters to send for detection (kept small for speed).

    Returns:
        Dict with has_data, has_policy, has_foresight booleans and reasons.
    """
    from backend.utils.document_parser import truncate_text
    from backend.services.llm_client import call_model

    truncated = truncate_text(text, max_chars)
    prompt = _OPTIONAL_DETECTION_PROMPT + truncated

    try:
        raw = call_model(prompt, max_new_tokens=128, temperature=0.1)
        parsed = _parse_detection_json(raw)
        return {
            "has_data": bool(parsed.get("has_data", True)),
            "has_policy": bool(parsed.get("has_policy", True)),
            "has_foresight": bool(parsed.get("has_foresight", True)),
            "reason_data": str(parsed.get("reason_data", "")),
            "reason_policy": str(parsed.get("reason_policy", "")),
            "reason_foresight": str(parsed.get("reason_foresight", "")),
        }
    except Exception:
        return {
            "has_data": True, "has_policy": True, "has_foresight": True,
            "reason_data": "检测失败，默认纳入",
            "reason_policy": "检测失败，默认纳入",
            "reason_foresight": "检测失败，默认纳入",
        }


def _parse_detection_json(raw: str) -> dict:
    """Parse JSON from LLM detection output, handling markdown code blocks."""
    import json as _json
    # Try extracting from ```json block first
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if m:
        return _json.loads(m.group(1))
    # Try extracting from first { to last }
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return _json.loads(m.group(0))
    return _json.loads(raw)


def assess_document_complexity(text: str) -> dict:
    """Quick heuristic assessment of document characteristics.

    Used by the evaluation service to adapt prompting strategy
    (e.g., stricter COT for dense academic papers, broader prompts for
    short policy briefs).

    Returns:
        Dict with keys: length, has_data, has_methodology, has_references,
        estimated_type, complexity_level.
    """
    text_lower = text.lower()
    length = len(text)

    # Detect signals
    has_data = bool(re.search(
        r"(?:数据|统计|%|百分比|样本|n\s*=|table|figure|图表)",
        text_lower,
    ))
    has_methodology = bool(re.search(
        r"(?:方法|模型|回归|分析|实证|定量|定性|研究设计|抽样)",
        text_lower,
    ))
    has_references = bool(re.search(
        r"(?:参考文献|引用|参见|出处|来源|footnote|endnote)",
        text_lower,
    ))

    # Estimate document type
    if length > 8000 and has_methodology and has_data:
        doc_type = "学术研究论文"
        complexity = "high"
    elif length > 3000 and (has_methodology or has_data):
        doc_type = "政策研究报告"
        complexity = "medium"
    elif length > 1000:
        doc_type = "政策简报或评论"
        complexity = "low"
    else:
        doc_type = "短文或摘要"
        complexity = "minimal"

    return {
        "length": length,
        "has_data": has_data,
        "has_methodology": has_methodology,
        "has_references": has_references,
        "estimated_type": doc_type,
        "complexity_level": complexity,
    }
