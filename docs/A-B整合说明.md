<!-- 整合说明：记录实际合并范围与静态核对，不把独立分支的运行记录算作本次验证。 -->
# A/B 后端整合交付

本次以 A 的 `a27793ab3c5bf7e57109e5e69fbbf3fc8027b772` 为底稿，合入 B 的 `408222c726eb7865d294cb155218b5234ffa5562`，保留双方提交历史。整合分支为 `codex/ab-integration`，经 PR 提交给 `main`。

## 已处理的边界

- 合并路由、菜单映射、公共模型，以及聊天、菜单、方案三个服务的六个文件冲突。
- 公共需求拒绝未知硬约束、非整数人数和矛盾的不辣人数；部分草稿保留跨轮状态、待确认字段与说明。没有总额/人均口径时继续追问。
- 单品的价格、角色、主食和小食餐份由 A 映射提供；周边商品、资格未知价格与无法解析的价格不进入可规划候选。
- `skill_adapter.py` 从 A 的完整套餐轮次核实默认选配，再转换为 B 的事实字典。保留每轮数量、选项、差价和实际组成，不把轮次名称当成无饮料证明。
- 同一份套餐的原价与现价都包含相同的规格差价；适配不改原菜单。未知差价、负应付或不可核实组成会排除该套餐。
- B 保留搜索、饭量模板、口味词表和优惠决策。选配兼容内部 `code`，公共输出仍用 `option_code`。
- B 按决策规则复核方案后，A 再按原始菜单及完整轮次独立核对实际餐份、饮料、组成、金额与预算，之后才保存。
- B 的 `CAPABILITIES` 声明由 A 的能力服务统一读取，并结合门店数据判断本次可用性。配置不再维护第二份写死的能力表。
- 正式模块失败或结果不合法时返回明确的 409，不用忽略饭量/口味的演示样例替代。旧样例仅供未装载正式模块的兼容场景，并明确标注来源。
- 保留 B 的浏览器演示入口与只读活动接口，正式前端仍由 C 负责。
- 当前为 `single_demo`：聊天变更和重新生成（含失败/无解）撤销旧方案；模拟结算继续使用服务端保存的金额，不产生真实交易。
- 保留双方忽略规则，`.workbuddy/`、`.qa_tmp/`、依赖、环境文件和运行期方案均不提交。

## 当前限制

套餐仅采用能够通过 A 核验的默认选配，尚未搜索任意替换组合。“不带饮料”使用可确认的主食单品，或默认选配本身不含饮料的套餐；默认带饮料的随心配不会被误判为无饮料。

价格仍为菜单参考价或演示快照价，不能标成实时收银核验。若在线菜单配用了历史套餐快照，响应说明会披露。

真实同篮子报价、卡费、会员资格和活动读取仍未接通。B 的相关纯计算函数保留，但 API 的完整能力不启用；会员结果以 `unavailable` 和 null 金额表示缺事实。营养可返回部分指标、覆盖率和缺项，完整营养能力标记仍保守关闭。

## 静态检查与待验收

本次按刘总要求，不编译、不启动后端、不运行 pytest、不重新调用 LLM 或麦当劳 MCP。只检查 Git 差异格式、冲突标记、导入/模型字段和关键调用链。下列用例已写入或更新，但没有执行：

- 默认套餐含饮料、蓝区/粉区、实际餐份与规格差价的事实边界。
- A 的默认套餐（含正差价与无限轮次数量）经过 B 规划后，再通过双方校验。
- 未知差价或换配后负价格的套餐在搜索前排除。
- 正式算法失败时明确拒绝，不绕过饭量和口味约束。
- B 既有解析、饭量、口味、营养、会员与模拟结算检查随分支合入。

B 文档中“70 项通过”属于 B 独立分支的历史记录，不能作为本整合版本通过运行验证的证据。`docs/api-examples/` 下 JSON 保留为既有分支采样，不宣称本次重新生成或验收。

## 实际文件清单

完整清单由本次相对 `main` 的 Git 差异列于下方；包含 A 的公共事实与快照、B 的模块，以及整合时必要的适配和说明。

共 53 个文件（相对当前 `origin/main`）：

- `.gitignore`
- `README.md`
- `backend/.env.example`
- `backend/api/routes.py`
- `backend/config.py`
- `backend/data/food_roles.json`
- `backend/data/snapshot_1400011.json`
- `backend/llm.py`
- `backend/main.py`
- `backend/providers/base.py`
- `backend/providers/mapper.py`
- `backend/providers/mcp_provider.py`
- `backend/providers/snapshot_provider.py`
- `backend/schemas.py`
- `backend/scripts/build_snapshot.py`
- `backend/scripts/probe_meal_detail.py`
- `backend/services/__init__.py`
- `backend/services/capabilities.py`
- `backend/services/chat_service.py`
- `backend/services/combo_service.py`
- `backend/services/meal_facts.py`
- `backend/services/menu_service.py`
- `backend/services/nutrition_service.py`
- `backend/services/plan_service.py`
- `backend/services/plan_validator.py`
- `backend/services/sample_planner.py`
- `backend/services/skill_adapter.py`
- `backend/skill/__init__.py`
- `backend/skill/campaigns.py`
- `backend/skill/membership.py`
- `backend/skill/models.py`
- `backend/skill/nutrition.py`
- `backend/skill/parser.py`
- `backend/skill/planner.py`
- `backend/skill/promotions.py`
- `backend/tests/test_acceptance.py`
- `backend/tests/test_b_integration.py`
- `backend/tests/test_plan_integrity.py`
- `backend/web/index.html`
- `docs/A-B整合说明.md`
- `docs/B模块实现与联调说明.md`
- `docs/api-examples/chat_no_beverage_draft.json`
- `docs/api-examples/chat_no_beverage_ready.json`
- `docs/api-examples/plans_no_beverage_demo.json`
- `docs/api-examples/raw/calculate_price.json`
- `docs/api-examples/raw/probe_summary.json`
- `docs/api-examples/raw/query_meal_detail.json`
- `docs/api-examples/raw/tools_list.json`
- `docs/创意与需求符合性审查.md`
- `docs/接口与模块契约.md`
- `tests/test_decision_extras.py`
- `tests/test_parser.py`
- `tests/test_planner.py`
