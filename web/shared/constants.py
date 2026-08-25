"""Evaluation indicator system for TCM policy literature review.

Defines the 7 primary indicators, 16 secondary indicators, 100-point
scoring rubric + optional additional items (±5 points), based on the
evaluation standard document.
"""

from typing import TypedDict


# ── Type definitions ───────────────────────────────────────────────
class SecondaryIndicator(TypedDict):
    """Structure of a single secondary indicator."""
    id: str           # e.g. "1.1"
    name: str          # e.g. "背景描述"
    max_score: int     # Maximum score for this indicator
    criteria: str      # What to evaluate
    scoring_guide: str # How to assign scores
    evidence_guide: str  # What constitutes each grade level


class PrimaryIndicator(TypedDict):
    """Structure of a primary indicator group."""
    id: str                          # e.g. "一"
    name: str                        # e.g. "研究背景与问题界定"
    weight: int                      # Total weight in 100-point system
    description: str                 # Overview of this group
    secondary: list[SecondaryIndicator]  # Sub-indicators


# ── Additional items (bonus points, summed into total score) ─────────
ADDITIONAL_ITEMS: list[dict] = [
    {
        "id": "bonus_discipline",
        "name": "学科适配性",
        "max_score": 5,
        "range": (0, 5),
        "description": "文献是否紧密围绕中医药政策核心议题，研究方法、数据来源、政策建议是否体现鲜明的中医药学科特色",
    },
    {
        "id": "bonus_methodology",
        "name": "方法学复杂度",
        "max_score": 3,
        "range": (0, 3),
        "description": "文献是否使用了进阶量化方法（如DID双重差分、ITS间断时间序列、多元回归、决策树/随机森林、DEA数据包络分析、PSM倾向性评分匹配、结构方程模型等）且适用得当",
    },
    {
        "id": "bonus_timeliness",
        "name": "政策时效性",
        "max_score": 2,
        "range": (0, 2),
        "description": "文献对应的政策议题是否属于当前改革热点（如DRG/DIP付费改革全国推行、中医药振兴发展重大工程等），实践参考价值是否具有时效性溢价",
    },
    {
        "id": "bonus_charts",
        "name": "图表质量",
        "max_score": 5,
        "range": (0, 5),
        "description": "文献中的表格和图表是否规范、清晰、信息丰富，能否有效辅助论证（含数据可视化、结构流程图、统计表格等）",
    },
]

# Backward-compat alias
ADDITIONAL_ITEM: dict = ADDITIONAL_ITEMS[0]
ADDITIONAL_MAX_TOTAL: int = sum(item["max_score"] for item in ADDITIONAL_ITEMS)

# ── 7 Primary × 16 Secondary indicator system (100 points) ─────────
PRIMARY_INDICATORS: list[PrimaryIndicator] = [
    {
        "id": "一",
        "name": "研究背景与问题界定",
        "weight": 15,
        "description": "评估文献对政策背景的梳理深度和问题界定的精准度",
        "secondary": [
            {
                "id": "1.1",
                "name": "背景描述",
                "max_score": 8,
                "criteria": (
                    "——是否系统梳理政策演变脉络，且包含近3年政策演变关键节点分析\n"
                    "——是否结合国内外典型案例分析政策出台的动因\n"
                    "——是否量化描述政策对社会、经济、政治的影响，且引用权威统计数据"
                ),
                "scoring_guide": (
                    "优秀7—8分：三项全达标，数据清晰、案例典型\n"
                    "良好5—6分：两项达标，量化分析或数据时效性较弱\n"
                    "一般3—4分：仅描述现象，无案例或数据支撑\n"
                    "差0—2分：背景模糊或与政策脱节"
                ),
                "evidence_guide": (
                    "优秀：有明确时间节点+典型案例+权威数据\n"
                    "良好：缺一项或数据较旧\n"
                    "一般：仅文字描述\n"
                    "差：背景与主题无关"
                ),
            },
            {
                "id": "1.2",
                "name": "问题界定",
                "max_score": 7,
                "criteria": (
                    "——是否通过利益相关者分析明确核心矛盾（如政府、企业、公众需求冲突）\n"
                    "——是否区分问题的紧迫性（短期矛盾）与长期性（结构性挑战）\n"
                    "——是否界定问题边界（如明确地域范围、目标群体、时间周期）"
                ),
                "scoring_guide": (
                    "优秀6—7分：精准定位问题层次，边界清晰、主次分明\n"
                    "良好4—5分：问题描述清晰但分类不足，边界部分模糊\n"
                    "一般2—3分：问题泛化，未聚焦核心痛点\n"
                    "差0—1分：问题界定错误或与政策目标无关"
                ),
                "evidence_guide": (
                    "优秀：有利益相关者矩阵+时间维度分析+明确边界\n"
                    "良好：缺一项或边界模糊\n"
                    "一般：问题描述空泛\n"
                    "差：问题与报告主题不符"
                ),
            },
        ],
    },
    {
        "id": "二",
        "name": "研究方法与数据",
        "weight": 20,
        "description": "评估研究方法论的科学性、数据来源的可靠性和分析过程的严谨性",
        "secondary": [
            {
                "id": "2.1",
                "name": "方法论",
                "max_score": 8,
                "criteria": (
                    "——是否混合使用定量（计量模型、统计分析）与定性方法（专家访谈、案例研究）\n"
                    "——所选方法是否与研究目标高度匹配（如因果推断需用实验设计，效果评估需用对比分析）\n"
                    "——是否说明方法局限性并提出改进措施"
                ),
                "scoring_guide": (
                    "优秀7—8分：方法多元、逻辑自洽，局限性分析透彻\n"
                    "良好5—6分：方法合理但创新性不足，改进措施较笼统\n"
                    "一般3—4分：方法单一或与研究问题部分脱节\n"
                    "差0—2分：方法不科学或未说明局限性"
                ),
                "evidence_guide": (
                    "优秀：定量+定性结合+方法匹配+局限性分析\n"
                    "良好：方法合理但缺局限性分析\n"
                    "一般：仅用一种方法\n"
                    "差：方法明显不当"
                ),
            },
            {
                "id": "2.2",
                "name": "数据来源",
                "max_score": 6,
                "criteria": (
                    "——是否包含权威机构数据（如统计局、卫统数据、中医药综合统计制度等）\n"
                    "——是否采用多源数据交叉验证\n"
                    "——是否标注数据获取时间、更新频率及处理方式"
                ),
                "scoring_guide": (
                    "优秀5—6分：数据多源、验证充分且时效性强（近3年）\n"
                    "良好3—4分：数据权威但验证不足，部分信息缺失\n"
                    "一般1—2分：来源单一或未标注关键信息\n"
                    "差0分：数据不可信或未标注来源"
                ),
                "evidence_guide": (
                    "优秀：权威来源+多源验证+完整标注\n"
                    "良好：权威但缺乏验证或标注不全\n"
                    "一般：单一来源\n"
                    "差：来源不明或数据可疑"
                ),
            },
            {
                "id": "2.3",
                "name": "数据分析",
                "max_score": 6,
                "criteria": (
                    "——是否说明分析工具\n"
                    "——是否检验数据信效度\n"
                    "——结论是否避免过度推断"
                ),
                "scoring_guide": (
                    "优秀5—6分：全流程透明，结论严谨、无过度推断\n"
                    "良好3—4分：分析合理但未公开工具或检验细节\n"
                    "一般1—2分：仅描述性统计，缺乏深度检验\n"
                    "差0分：数据与结论无关或推断过度"
                ),
                "evidence_guide": (
                    "优秀：工具公开+信效度检验+结论谨慎\n"
                    "良好：分析合理但缺部分细节\n"
                    "一般：只有基础统计\n"
                    "差：结论与数据脱节"
                ),
            },
        ],
    },
    {
        "id": "三",
        "name": "政策建议可行性",
        "weight": 18,
        "description": "评估政策建议的可操作性、成本效益和风险把控",
        "secondary": [
            {
                "id": "3.1",
                "name": "可操作性",
                "max_score": 6,
                "criteria": (
                    "——是否明确责任主体与分工（如部委、地方政府的权责划分）\n"
                    "——是否设计分阶段实施路线图（如试点周期、推广条件）\n"
                    "——是否与现行政策体系兼容（如上位法、地方条例衔接）"
                ),
                "scoring_guide": (
                    "优秀5—6分：框架完整，路径清晰且兼容性强\n"
                    "良好3—4分：有分工但阶段规划模糊，兼容性部分存疑\n"
                    "一般1—2分：建议抽象，缺乏执行细节\n"
                    "差0分：不可操作或与现行政策冲突"
                ),
                "evidence_guide": (
                    "优秀：责任明确+路线图清晰+政策兼容\n"
                    "良好：有分工但缺时间规划\n"
                    "一般：建议笼统\n"
                    "差：与现行政策矛盾"
                ),
            },
            {
                "id": "3.2",
                "name": "成本效益",
                "max_score": 6,
                "criteria": (
                    "——是否测算财政支出规模与资金来源（如中央/地方分摊比例）\n"
                    "——是否量化社会效益（如就业增长、环境改善指标）\n"
                    "——是否对比多方案性价比（如成本收益比、风险回报率）"
                ),
                "scoring_guide": (
                    "优秀5—6分：效益量化精准，方案对比充分\n"
                    "良好3—4分：定性说明效益，方案对比不足\n"
                    "一般1—2分：成本或效益分析缺失\n"
                    "差0分：无经济可行性论证"
                ),
                "evidence_guide": (
                    "优秀：有成本测算+效益量化+方案对比\n"
                    "良好：有定性说明但无量化\n"
                    "一般：只提成本或效益之一\n"
                    "差：无经济分析"
                ),
            },
            {
                "id": "3.3",
                "name": "风险评估",
                "max_score": 6,
                "criteria": (
                    "——是否识别经济、社会、政治多维风险\n"
                    "——是否建立风险预警指标\n"
                    "——是否预设风险应对预案"
                ),
                "scoring_guide": (
                    "优秀5—6分：风险全覆盖，预警指标明确，预案具体可执行\n"
                    "良好3—4分：识别风险但预警指标或应对措施模糊\n"
                    "一般1—2分：仅列举风险类型，无具体分析\n"
                    "差0分：未提及风险或预案"
                ),
                "evidence_guide": (
                    "优秀：多维度风险+预警指标+具体预案\n"
                    "良好：有风险识别但缺预警或预案\n"
                    "一般：只列风险类型\n"
                    "差：无风险分析"
                ),
            },
        ],
    },
    {
        "id": "四",
        "name": "逻辑结构与论证",
        "weight": 12,
        "description": "评估文献的逻辑严密性和论证充分性",
        "secondary": [
            {
                "id": "4.1",
                "name": "逻辑性",
                "max_score": 7,
                "criteria": (
                    "——是否采用「问题—原因—对策」递进结构\n"
                    "——是否避免循环论证或跳跃式推论\n"
                    "——图表与文字是否互补支撑"
                ),
                "scoring_guide": (
                    "优秀6—7分：逻辑闭环，推理严密，图文深度融合\n"
                    "良好4—5分：结构清晰但部分衔接松散\n"
                    "一般2—3分：逻辑断裂或重复论证\n"
                    "差0—1分：结构混乱，难以理解"
                ),
                "evidence_guide": (
                    "优秀：递进结构+无逻辑漏洞+图文配合好\n"
                    "良好：结构清晰但偶有跳跃\n"
                    "一般：逻辑不够连贯\n"
                    "差：结构混乱"
                ),
            },
            {
                "id": "4.2",
                "name": "论证充分性",
                "max_score": 5,
                "criteria": (
                    "——是否引用国内外前沿研究成果\n"
                    "——是否对比正反方观点并回应\n"
                    "——是否结合政策实践案例"
                ),
                "scoring_guide": (
                    "优秀4—5分：论据多元，驳斥对立观点有力\n"
                    "良好2—3分：引用充分但缺乏批判性分析\n"
                    "一般1分：论据单薄或案例关联性弱\n"
                    "差0分：无文献或案例支撑"
                ),
                "evidence_guide": (
                    "优秀：前沿文献+正反对比+实践案例\n"
                    "良好：引用充分但缺批判\n"
                    "一般：论据较少\n"
                    "差：无论据支撑"
                ),
            },
        ],
    },
    {
        "id": "五",
        "name": "创新性与前瞻性",
        "weight": 10,
        "description": "评估研究的创新程度和对未来趋势的预判能力",
        "secondary": [
            {
                "id": "5.1",
                "name": "创新性",
                "max_score": 6,
                "criteria": (
                    "——是否提出新理论框架或政策工具\n"
                    "——是否挑战传统政策思维定式\n"
                    "——是否融合跨领域经验"
                ),
                "scoring_guide": (
                    "优秀5—6分：原创性强，跨界融合自然\n"
                    "良好3—4分：局部创新但理论深度不足\n"
                    "一般1—2分：重复已有研究，无明显新意\n"
                    "差0分：无创新"
                ),
                "evidence_guide": (
                    "优秀：原创框架/工具+突破传统+跨界融合\n"
                    "良好：有新意但不够深入\n"
                    "一般：无明显创新\n"
                    "差：完全重复"
                ),
            },
            {
                "id": "5.2",
                "name": "前瞻性",
                "max_score": 4,
                "criteria": (
                    "——是否预判技术、社会趋势对政策的影响（如AI、老龄化）\n"
                    "——是否设计弹性政策机制（如动态调整、定期评估周期）"
                ),
                "scoring_guide": (
                    "优秀3—4分：趋势预判精准，方案适应性强\n"
                    "一般1—2分：描述趋势但无具体应对措施\n"
                    "差0分：无前瞻性分析"
                ),
                "evidence_guide": (
                    "优秀：趋势预判+弹性机制设计\n"
                    "良好：有趋势分析但缺机制\n"
                    "一般：仅提及趋势\n"
                    "差：无前瞻性"
                ),
            },
        ],
    },
    {
        "id": "六",
        "name": "语言表达与格式",
        "weight": 10,
        "description": "评估文献的语言规范性和格式标准化程度",
        "secondary": [
            {
                "id": "6.1",
                "name": "语言表达",
                "max_score": 6,
                "criteria": (
                    "——是否使用政策术语规范\n"
                    "——是否避免学术化晦涩表达\n"
                    "——摘要是否体现核心结论"
                ),
                "scoring_guide": (
                    "优秀5—6分：术语准确、通俗易懂、摘要精练\n"
                    "良好3—4分：语言通顺但摘要冗长或术语偶有不当\n"
                    "一般1—2分：表述模糊或术语错误较多\n"
                    "差0分：语义混乱或摘要缺失"
                ),
                "evidence_guide": (
                    "优秀：术语规范+通俗易懂+摘要精练\n"
                    "良好：语言通顺但有小瑕疵\n"
                    "一般：表达不够清晰\n"
                    "差：难以理解"
                ),
            },
            {
                "id": "6.2",
                "name": "格式规范",
                "max_score": 4,
                "criteria": (
                    "——是否遵循《党政机关公文格式》（GB/T 9704-2012）\n"
                    "——图表是否编号并附数据来源说明\n"
                    "——参考文献是否包含政策原文"
                ),
                "scoring_guide": (
                    "优秀3—4分：全项达标，格式严谨无瑕疵\n"
                    "良好2分：基本规范但细节缺失（如图表未编号）\n"
                    "差0—1分：格式错误严重影响阅读"
                ),
                "evidence_guide": (
                    "优秀：格式完全符合标准\n"
                    "良好：基本符合但有小问题\n"
                    "一般：格式问题较多\n"
                    "差：格式混乱"
                ),
            },
        ],
    },
    {
        "id": "七",
        "name": "实际应用价值",
        "weight": 15,
        "description": "评估文献对政策制定和社会发展的实际影响",
        "secondary": [
            {
                "id": "7.1",
                "name": "政策影响",
                "max_score": 9,
                "criteria": (
                    "——是否被决策部门采纳或写入征求意见稿\n"
                    "——是否设计配套实施细则（如考核指标）\n"
                    "——是否通过政策模拟验证效果（如SWOT分析）"
                ),
                "scoring_guide": (
                    "优秀8—9分：已被采纳或进入决策流程\n"
                    "良好5—7分：方案完整但未实践，模拟验证较充分\n"
                    "一般2—4分：仅有理论建议，模拟验证不足\n"
                    "差0—1分：脱离现实需求"
                ),
                "evidence_guide": (
                    "优秀：已采纳/进入流程+配套方案+模拟验证\n"
                    "良好：方案完整+有模拟\n"
                    "一般：理论建议为主\n"
                    "差：脱离实际"
                ),
            },
            {
                "id": "7.2",
                "name": "社会影响",
                "max_score": 6,
                "criteria": (
                    "——是否引发主流媒体或学术圈讨论（如专题报道、论文引用）\n"
                    "——是否设计公众参与渠道\n"
                    "——是否评估对弱势群体的保护效应"
                ),
                "scoring_guide": (
                    "优秀5—6分：推动社会共识并促进公平\n"
                    "良好3—4分：引发讨论但参与机制缺失\n"
                    "一般1—2分：社会价值不明确\n"
                    "差0分：无社会价值"
                ),
                "evidence_guide": (
                    "优秀：媒体关注+参与机制+公平评估\n"
                    "良好：有社会关注但缺机制\n"
                    "一般：社会价值不明显\n"
                    "差：无社会意义"
                ),
            },
        ],
    },
]

# ── Optional indicators ──────────────────────────────────────────────
# Secondary indicators that may be excluded based on document content,
# organized by detection group. Each group maps to a document feature
# that must be detected for the indicators to be included.
#
# Groups:
#   data      — 数据来源(2.2), 数据分析(2.3): require data content
#   policy    — 可操作性(3.1), 成本效益(3.2), 风险评估(3.3): require policy recommendations
#   foresight — 前瞻性(5.2): require forward-looking / predictive content
OPTIONAL_GROUPS: dict[str, set[str]] = {
    "data": {"2.2", "2.3"},
    "policy": {"3.1", "3.2", "3.3"},
    "foresight": {"5.2"},
}

# All optional secondary indicator IDs (flat set)
OPTIONAL_SECONDARY_IDS: set[str] = set().union(*OPTIONAL_GROUPS.values())


def get_active_secondary_indicators(
    has_data: bool = True,
    has_policy: bool = True,
    has_foresight: bool = True,
) -> list[SecondaryIndicator]:
    """Return secondary indicators to evaluate, excluding inapplicable ones."""
    excluded: set[str] = set()
    if not has_data:
        excluded |= OPTIONAL_GROUPS.get("data", set())
    if not has_policy:
        excluded |= OPTIONAL_GROUPS.get("policy", set())
    if not has_foresight:
        excluded |= OPTIONAL_GROUPS.get("foresight", set())
    if not excluded:
        return list(ALL_SECONDARY_INDICATORS)
    return [s for s in ALL_SECONDARY_INDICATORS if s["id"] not in excluded]


def get_excluded_indicator_ids(
    has_data: bool = True,
    has_policy: bool = True,
    has_foresight: bool = True,
) -> list[str]:
    """Return sorted list of excluded secondary indicator IDs."""
    excluded: set[str] = set()
    if not has_data:
        excluded |= OPTIONAL_GROUPS.get("data", set())
    if not has_policy:
        excluded |= OPTIONAL_GROUPS.get("policy", set())
    if not has_foresight:
        excluded |= OPTIONAL_GROUPS.get("foresight", set())
    return sorted(excluded)


def get_active_primary_indicators(
    has_data: bool = True,
    has_policy: bool = True,
    has_foresight: bool = True,
) -> list[PrimaryIndicator]:
    """Return primary indicators with active secondary indicators.

    A primary indicator is included if at least one of its secondaries
    is active.  Its effective weight is recomputed from active secondaries.
    """
    active_secondary_ids = {
        s["id"]
        for s in get_active_secondary_indicators(has_data, has_policy, has_foresight)
    }
    result: list[PrimaryIndicator] = []
    for p in PRIMARY_INDICATORS:
        active_subs = [s for s in p["secondary"] if s["id"] in active_secondary_ids]
        if not active_subs:
            continue
        effective_weight = sum(s["max_score"] for s in active_subs)
        result.append({
            **p,
            "weight": effective_weight,
            "secondary": active_subs,
        })
    return result


# ── Derived helpers ─────────────────────────────────────────────────
# Flat list of all secondary indicators for iteration
ALL_SECONDARY_INDICATORS: list[SecondaryIndicator] = [
    s for p in PRIMARY_INDICATORS for s in p["secondary"]
]

# Max possible total (without additional item)
MAX_TOTAL_SCORE: int = sum(p["weight"] for p in PRIMARY_INDICATORS)

# Map secondary indicator ID -> primary indicator for aggregation
SECONDARY_TO_PRIMARY: dict[str, str] = {}
for p in PRIMARY_INDICATORS:
    for s in p["secondary"]:
        SECONDARY_TO_PRIMARY[s["id"]] = p["id"]


def get_secondary_by_id(indicator_id: str) -> SecondaryIndicator | None:
    """Look up a secondary indicator by its ID (e.g. '3.2')."""
    for s in ALL_SECONDARY_INDICATORS:
        if s["id"] == indicator_id:
            return s
    return None


def get_primary_by_id(primary_id: str) -> PrimaryIndicator | None:
    """Look up a primary indicator by its ID (e.g. '三')."""
    for p in PRIMARY_INDICATORS:
        if p["id"] == primary_id:
            return p
    return None


# ── Overall assessment thresholds (100-point scale) ────────────────
OVERALL_THRESHOLDS: list[tuple[float, str]] = [
    (90.0, "该文献质量优秀，在绝大多数评价指标上表现突出，具有很高的学术价值和政策参考意义。"),
    (80.0, "该文献质量良好，多数指标表现较好，部分维度有提升空间。"),
    (70.0, "该文献质量中等，部分指标表现尚可，多个维度需要进一步加强。"),
    (60.0, "该文献质量一般，在研究方法、理论深度和规范性方面存在不足。"),
    (0.0, "该文献质量有待提升，多项指标表现较差，建议在核心维度上大幅改进。"),
]

# ── History limits ─────────────────────────────────────────────────
MAX_HISTORY_RECORDS: int = 50
MAX_DISPLAY_RECORDS: int = 20

# ── Document processing ────────────────────────────────────────────
MAX_DOC_CHARS: int = 12000
PREVIEW_CHARS: int = 3000

# ── LLM generation ─────────────────────────────────────────────────
MAX_NEW_TOKENS: int = 512
DEFAULT_TEMPERATURE: float = 0.3
DEFAULT_TOP_P: float = 0.9
