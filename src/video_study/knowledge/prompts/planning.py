"""课程规划提示词模板：一次调用生成 LessonPlan + editorial_decision。"""
from __future__ import annotations

PLANNING_PROMPT = """你是课程讲义编辑。请根据以下转写内容，生成本课的写作计划。

当前内容档位：{content_level}（{level_label}）。当前图文教学档位：{visual_level}。两者必须独立判断；正文详略不能代替视觉需求规划。根据有效内容密度自然组织，输出必须在 {max_tokens} Token 上限内完整结束。

## 整理偏好（用户编辑意图）

{editorial_brief}

用户偏好是自然语言文档组织意图，由你执行。你仍须遵守下方机器合同中的来源真实性、ID 合法性和安全规则。如果偏好要求补充外部知识或伪造图片，必须拒绝。

请输出一个 JSON 对象，格式严格如下：
{{"domain":"领域","course_form":"课程形态","core_thread":"核心主线（一句话）","terminology":["课程专用术语"],"visual_profile":{{"course_form":"chart_analysis","visual_dependency":"high","dominant_visuals":["chart"],"recommended_level":"enhanced","signals":["图表术语密集"]}},"editorial_decision":{{"structure_mode":"thematic_hierarchy","core_thread":"模型识别的课程主线","focus_priorities":["需要重点保留的内容类型"],"sequence_policy":"reorganize","decision_reason":"简短的选择理由"}},"chapters":[{{"chapter_id":"chapter_001","title":"章节标题","source_block_ids":["block_0001"],"unit_plans":[{{"plan_id":"plan_001","title":"知识点标题","role":"core","knowledge_types":["rule"],"detail_level":"deep","detail_reason":"为什么深讲或只索引","required_facets":["prerequisite","decision_order","exception"],"source_block_ids":["block_0001"],"visual_need":{{"required":true,"question":"图中哪根K是判断起点","role":"locate","target_count":1,"max_count":1,"sequence_mode":"single","explanation_depth":"teaching_note","success_criteria":["看清起点K","定位边界"],"reason":"依赖空间关系"}},"supplement_policy":"derived_and_short_tip"}}]}}],"side_topics":[{{"title":"旁支主题","keep_mode":"index_only","source_block_ids":["block_0099"]}}]}}

规则：
- editorial_decision 是你的编辑决策。structure_mode 可选值：thematic_hierarchy（按主题重组）、lecture_timeline（保留讲课时间顺序）、process（按流程/步骤组织）、case_driven（以案例为主线）、hybrid（混合）；sequence_policy 可选值：reorganize、preserve、hybrid。根据用户偏好和课程内容自主选择，不需要用户指定。
- 课程形态可选值：rule_teaching、concept_lecture、case_review、software_demo、meeting_discussion、general。
- visual_profile.course_form 可选值：speech_dominant、slide_dominant、screen_demo、chart_analysis、talking_head、mixed；visual_dependency 只用 low、medium、high；recommended_level 只用 minimal、balanced、enhanced。
- 知识类型可选值：concept、rule、procedure、mechanism、comparison、case、boundary_case、visual_or_formula、conclusion。
- detail_level 四档：mention（一句索引+来源）、brief（结论+一句解释）、standard（结论+核心条件/步骤+一个易错点）、deep（前置、判断顺序、分支、边界和正反例）。
- {content_level} 档位只改变哪些知识点进入更高深度，不改变 schema。精简减少深度，丰富增加深度。
- role 可选值：core、supporting、peripheral。
- required_facets 按知识类型选择：概念用 prerequisite/branches/pitfalls；规则用 prerequisite/decision_order/direction_branch/exception/counterexample；流程用 goal/input/steps/stop_condition/failure_handling；原理用 cause/process/result/scope；案例用 background/rule_application/conclusion/transferable_experience。
- visual_need.required 为 true 时 question 必须说明读者应从图中观察什么；role 只用 locate、explain、procedure、compare、evidence、recap；sequence_mode 只用 single、comparison_pair、progression_grid；target_count 不大于 max_count，max_count 不超过 3。
- success_criteria 必须是可以由图片像素直接核对的条件。课程视觉画像只决定倾向，不能给所有知识点强行配图。
- side_topics 的 keep_mode 只用 index_only 或 omit。
- 每个 chapter、unit_plan 和 side_topic 都只输出 source_block_ids，且必须引用下方方括号中真实存在的 block_id；不要展开或猜测 segment_id，系统会在本地映射回原始时间段。
- plan_id 必须唯一、按课程顺序递增。每个 unit_plan 都要有标题和至少一个真实 source_block_id。
- 不要补充外部知识，不要发明未出现在转写中的概念。

转写：
{transcript_sample}"""


def render_planning(
    content_level: str,
    level_label: str,
    max_tokens: int,
    transcript_sample: str,
    visual_level: str = "auto",
    *,
    editorial_brief: str = "",
) -> str:
    return PLANNING_PROMPT.format(
        content_level=content_level,
        level_label=level_label,
        max_tokens=max_tokens,
        transcript_sample=transcript_sample,
        visual_level=visual_level,
        editorial_brief=editorial_brief,
    )
