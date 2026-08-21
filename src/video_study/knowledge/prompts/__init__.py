"""V4 唯一云端 writer 提示词：消费紧凑 CourseIR + 编辑意图。"""
from __future__ import annotations

__all__ = ["compose_course_ir_prompt"]


def compose_course_ir_prompt(
    *,
    payload_json: str,
    content_level: str,
    max_tokens: int,
    editorial_brief: str = "",
    editorial_decision: str = "",
) -> str:
    """渲染紧凑 CourseIR writer 提示词。"""
    brief_section = ""
    if editorial_brief.strip():
        brief_section = f"""

## 整理偏好（用户编辑意图）

{editorial_brief}

用户偏好是自然语言文档组织意图，由你执行。你仍须遵守下方机器合同中的来源真实性、ID 合法性和安全规则。如果偏好要求补充外部知识或伪造图片，必须拒绝。"""

    decision_section = ""
    if editorial_decision.strip():
        decision_section = f"""

## 编辑决策（规划阶段生成）

{editorial_decision}

编辑决策描述了本课的结构选择和重点。你在精炼正文时应遵循该决策，在安全合同范围内调整结构和详略。"""

    return f"""你是课程讲义编辑。只根据下面的 CourseIR JSON 组织中文讲义，不补充外部事实。
当前内容档位：{content_level}。必须在 {max_tokens} Token 内输出完整 JSON。{brief_section}{decision_section}

CourseIR 字段：sources 是可引用来源与时间；units 是必须逐项覆盖的知识点，unit.src 是来源 ID，内部 claims 按来源/origin 分组保存原子事实；claim 分组未写 source_ids 时继承 unit.src，未写 origin 时默认为 audio_backed；visuals 是本地已核实图片事实。每个 claim 只能进入一个主要 content_block，禁止把同一句同时放进 paragraph 与 rule_list/steps。标题不得复制正文开头。
你可以自主选择 paragraph、rule_list、steps、example、pitfall 和 visual_group；来源没有对应信息时不必输出该栏目。你也可以自主决定是否需要学习目标、章节摘要、复习清单或未展开问题。详略由知识的重要程度和课程强调程度决定，不平均分配篇幅。
visuals 只允许按 visual_id 放置；不得创建新图片、frame 或来源。不要输出本地路径、Markdown、PDF、DOCX 或图片数据。
涉及公式、换元、微分或积分步骤时，必须逐行核对等价关系、运算顺序和常数因子；不得把两个不同等价改写中的系数拼接到同一公式。来源不足以确认精确公式时，只保留有来源支撑的解题思路，并在 open_questions 标为待回看，不得补造精确等式。

输出格式：
{{"document_title":"资料标题","overview":"2-4句导览","learning_objectives":["目标"],"sections":[{{"title":"章节标题","summary":"章节摘要","knowledge_points":[{{"plan_id":"unit id","statement":"短标题","explanation":"正文摘要","content_blocks":[{{"block_id":"content_001","type":"paragraph|rule_list|steps|example|pitfall|visual_group","origin":"audio_backed|visual_backed|model_aid","text":"文本","items":[],"claim_ids":["claim id"],"source_ids":["source id"],"binding_id":"visual id"}}],"facet_status":{{}},"editorial_note":"","review_tip":"","source_block_ids":["source id"]}}],"visual_bindings":[]}}],"review":{{"knowledge_thread":"主线","checklist":["要点"],"open_questions":["来源未讲清问题"]}}}}

规则：每个 unit id 恰好输出一次；所有 source/unit/claim/visual ID 必须来自输入集合；audio_backed 块必须引用 claim_ids 与 source_ids；visual_group 只能引用输入 visual_id。不得输出 JSON 外文字。

CourseIR:
{payload_json}"""
