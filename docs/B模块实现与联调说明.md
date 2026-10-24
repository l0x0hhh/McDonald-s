# B 模块实现与联调说明

> 2026-10-24 功能分支状态。V0.5 项目方案仍是验收目标；原《接口与模块契约》记录开工时的旧基线，V1.1《接口扩展草案》记录目标设计。以下内容以功能分支代码和本地测试为准，待 A 审查后合入 main。

## 用户怎样设置“不带饮料”

用户明确说“不要饮料”“不喝饮品”等，`POST /api/v1/chat` 返回的 `draft.exclude_beverage` 与完整 `requirements.exclude_beverage` 就是 `true`。用户在可编辑表单里勾选“不带饮料”也应提交 `true`。用户未表达此限制时默认 `false`；“可以喝饮料”等更正会改回 `false`，其他已确认需求保留。

`true` 是整单硬约束：饮料单品、证实含饮品的套餐、组成或最终选配未知的套餐均不能入选。无可行组合时返回 `plans=[]` 和 `unsatisfiable_reason`，不会自动把茶、牛奶或无糖饮品当成“无饮料”。

## 当前可用范围

| 能力 | 当前工作区行为 |
|---|---|
| 需求提取 | 异步 `backend.llm.extract_requirements(message, current)` 返回完整需求、跨轮 `draft`、缺失字段、追问、来源与说明；在线模型需配置凭据，演示模式使用固定规则并标 `demo_template`。模型异常回退规则时标 `rule`。人数、预算口径、忌辣人数冲突会追问；其他忌口显示支持范围。 |
| 正式 Skill | `backend.skill.generate_plans(items, requirements, nutrition, *, context=None)` 返回 `(list[PlanOut], 无解原因)`。最多三套不同组合，按有限候选的最省、搭配多样、口味匹配视角排序；预算为整数分。服务层重新核对门店商品、数量、价格、优惠、餐份与硬约束后才保存。 |
| 饭量 | 轻：每人至少 1 份主食；正常：每人至少 1 份主食、每 2 人至少 1 份小食或额外主食；大：每人至少 2 份主食、每 2 人至少 1 份小食或额外主食。这些是可解释的产品模板，并非营养标准。预算不足就说明无解。 |
| 口味 | 牛肉、鸡肉、鱼、炸物、烤制、甜食、无糖从商品名称或已核实套餐成分匹配；不使用 MCP 的“上新”“麦金卡”等营销标签作为口味。未识别偏好会说明。 |
| 开卡 | B 的 `compare_membership` 对同商品、同数量、同规格的不开卡、已有卡、新开卡报价计算卡费和净收益，负数照实返回；缺事实为 `status=unavailable` 且未知金额为 `null`。现有菜单快照没有已核实三情境报价与卡费，真实接口目前只返回不可计算状态。 |
| 习惯 | 仅用用户自报每月次数和分析周期；只有固定篮子重复适用、有效期、次数上限及互斥条件都经核实时，才计算条件性回本。 |
| 营养 | 按最终单品或已核实套餐成分、规格、数量逐项汇总；每个指标独立给已知份数、缺项与来源。未知不计零，缺纤维不作纤维偏低判断。替换建议仅由 B 重建并验证另一篮子后生成。 |
| 活动/新品 | B 的 `recommend_campaigns` 只筛选 A 提供的有来源、时间、门店和参与条件的真实只读数据。当前未接活动读取事实，`GET /api/v1/campaigns` 返回 `status=unavailable`。 |

套餐选配、在线算价、会员卡费/资格和活动读取属于 A 的数据事实。现有 `MenuService.get_planning_context` 在缺事实时返回空的 `combo_details`、`membership_facts`、`campaigns`；不会凭名称、菜单原价或用户自报会员状态编造结果。在线菜单来源 `live_mcp` 不等于收银报价；方案 `price_basis=menu_estimate` 表示参考估算。演示快照标为 `demo_snapshot`。

## HTTP 对接

原 `code/data/msg` 包装和路径保留。`GET /api/v1/config` 新增 `contract_version=1.1`、`capabilities`、`data_limitations`。`POST /api/v1/chat` 的 `current` 接受上轮 `draft` 或完整需求；仅当 `ready=true` 且 `requirements` 非空才请求 `/plans`。`POST /api/v1/plans` 保留 `store_code` 和完整 `requirements`，新增 `algorithm_source=skill`、每套方案的 `membership_comparison`、`nutrition_summary` 与 `price_basis`。未知硬约束字段返回 422；Skill 缺失或结果未通过校验返回 409 `E_CAPABILITY_UNAVAILABLE`，不会用旧样例覆盖请求。`POST /api/v1/orders` 仍是 `status=simulated`、`is_demo=true`。

示例需求：

```json
{
  "store_code": "1400011",
  "requirements": {
    "people": 2,
    "budget_type": "total",
    "budget_fen": 10000,
    "dietary_constraints": [{"kind": "non_spicy", "people": 1}],
    "exclude_beverage": true,
    "appetite": "normal",
    "preferred_tags": ["chicken"]
  }
}
```

浏览器输入页在方案生成后再次提交时默认开启新需求，避免把上一单的忌口或饮品条件带到新单；只有勾选“修改上一份已生成的需求”才传递旧 `draft`。信息不足时的下一句会自动延续当前草稿。页面只显示已设定的无饮料约束，正常饭量标注为默认模板。用户改需求或重新生成方案时，当前单会话演示服务撤销旧方案；旧 `plan_id` 无法用于模拟结算。多用户会话隔离尚未实现，`config.session_scope=single_demo` 如实标示。

## A 向 B 注入的事实

`PlanningContext` 由服务端提供，HTTP 客户端不能自行提交。套餐详情按商品 code 放入 `combo_details`。固定套餐至少提供 `verified=true` 和非空 `components`，每个成分含真实 `code`、`name`、`quantity`、`food_role`、`beverage_status`，主食需 `servings` / `non_spicy_servings`，小食需 `side_servings`。可选配套餐还需 `rounds`、结构化 `selected_options`、已核实最终 `price_fen` 和 `selection_components_verified=true`，证明最终成分与所选轮次一致。缺一项时 B 保守排除。

优惠事实放 `promotions`，需商品 code、减免分数、适用门店及 `verified=true`、`eligible=true`。资格或互斥关系不明时不应用；当前最多应用一条。正式报价需 A 核实工具返回、按选配报价并重新校验总价；仅有菜单价不能标 `live_quote`。

会员事实放 `membership_facts`。`without_card`、`existing_card`、`new_card` 三个报价都要有相同篮子的 code/数量/结构化规格，以及 `payable_fen`、`verified`、`eligible`、`price_basis`、`source`、带时区 `fetched_at`。顶层提供已核实 `card_fee_fen`、`card_fee_verified`，并明确新开卡报价是否已含卡费；缺数为不可计算。习惯分析还需已核实有效期、次数限制、重复篮子资格与互斥条件。

活动事实放 `campaigns`，至少提供来源、带时区起止时间、适用门店、账号/会员条件和独立的 `taste_tags`。原 MCP 营销标签不得直接当作食材口味。

## 验证与限制

在仓库根目录执行 `python -B -m pytest tests backend/tests -q`；本地 69 项通过，其中包括浏览器输入页、新旧需求隔离、1～7 人主食份数、演示快照下的聊天草稿、无饮料方案、服务端校验、模拟结算和旧方案失效。`docs/api-examples/chat_no_beverage_draft.json`、`chat_no_beverage_ready.json`、`plans_no_beverage_demo.json` 是当前接口在演示模式下生成的完整响应。在线模型的本地探测被当前执行环境的网络权限阻止，不能据此判断用户终端的配置无效；真实 MCP 套餐选配、实时算价、会员卡费与活动读取尚未实测。本次 Codex 工作不冒充 WorkBuddy 会话，真实记录由本人另行整理。
