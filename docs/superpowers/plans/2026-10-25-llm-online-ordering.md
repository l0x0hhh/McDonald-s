# 小厨房在线模型配餐与支付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实菜单和应用 Skill 驱动模型配餐，移除离线入口，接通官方待支付订单，改善字号文案。

**Architecture:** 后端提供完整实时菜单及选配给模型，模型只选餐，服务端重建事实并官方算价。独立 SQLite 订单记录承担报价版本、请求幂等和支付状态；前端只调用在线接口并导航官方支付链接。

**Tech Stack:** Python/FastAPI/Pydantic/httpx/SQLite；React/TypeScript/Vite/Motion；官方 Codex CLI（ChatGPT 订阅，默认）；Responses API（显式选用）；McDonald's MCP。

**Spec:** `G:/program/McDonald-s/docs/superpowers/specs/2026-10-25-llm-ordering-design.md`

## Global Constraints

- 沿用当前 `codex/frontend-kitchen-ui`；保护当前未提交成果；不自动提交或推送。
- 前端 5173，后端 18000，不占用 8000。
- 不复制或输出凭据；应用凭据仅来自服务端环境。
- 不调用旧搜索、固定排序或运行时样例；测试夹具仅在测试中使用。
- 用户确认页面按钮才触发真实创建；自动联调只读、调用模型与算价，不创建订单或付款。
- 订单未知结果不自动重试；官方状态才构成已支付证明。
- 保留现有红黄厨房、3D 按钮、厨师和弹簧动效。

## Review Focus

- 同款不同选配：允许独立商品行，不能按商品码串价；任务 1/2 测试。
- 报价变化或预算变更：停止创建，显示新价格并等待再次选择；任务 2/3 测试。
- 双击、并发、重启、解码失败：原子幂等和提交未知，不重复创建；任务 2 测试。
- 模型失败或断网：明确错误，旧搜索与样例从未调用；任务 1/3 测试。
- 迟到响应、支付返回、200% 缩放：不意外导航、不假称支付、文字不遮挡；任务 3/4 测试。

## 文件所有权与接口

- 主代理：`backend/schemas.py`、`backend/api/routes.py`、共享契约及集成测试/报告。
- 模型后端实施者：`backend/services/llm_planner.py`、`backend/services/plan_service.py`、`backend/skills/meal-planner/SKILL.md`、模型边界与在线菜单/配置，以及对应测试。
- 支付后端实施者：`backend/services/checkout_service.py`、`backend/services/order_repository.py`、`backend/providers/checkout_provider.py`，以及对应测试。公共 routes 由主代理接线。
- 前端实施者：`frontend/src/**`、相关前端测试和 README；不修改后端文件。

共享接口：

- `LLMPlanner.generate(items, requirements, context) -> list[PlanOut]`：异步，返回事实重建后的菜单参考方案，不排序，不调用旧规划器。
- `CheckoutService.quote(plan, store_code, requirements) -> PlanOut`：异步，增加官方报价字段并校验预算，保存报价版本。
- `CheckoutService.create_order(payload: OrderIn) -> OrderOut`：异步，按已存报价核验、幂等创建，不接收客户端商品或金额。
- `CheckoutService.get_order(order_id: str) -> OrderOut`：异步，官方状态查询。
- `PlanOut` 增加 `quote_id / quoted_at / take_way_options / quote_breakdown`；`PlanItemOut` 增加 `line_price_basis`。
- `OrderIn`：`plan_id / quote_id / take_way_code / idempotency_key`。
- `OrderOut`：`order_id / plan_id / store_code / total_fen / status / payment_url / expires_at`。
- 对外契约 1.2，`algorithm_source="llm_skill"`；所有生产数据 `data_source="live_mcp"`。

### Task 1: Skill 与在线模型配餐

**Files:** 新建 Skill、`llm_planner.py`；修改 `plan_service.py`、模型调用配置、capabilities、菜单/套餐/营养运行时数据来源；新增模型与纯在线回归。

- [ ] 编写失败测试：模型候选成为实际购物篮、保留顺序、不凑三套、合法非默认选配可用、同款多规格可分行。
- [ ] 编写失败测试：伪商品/差价/未知必选参数被拒，旧 `_search` 与样例入口调用即失败，模型错误无回退。
- [ ] 运行专项测试并保留 RED 原始输出。
- [ ] 实现 Responses 严格结构化候选、加载 Skill 版本/哈希和最多一次修正；候选仅填代码/数量/选配/简短理由。
- [ ] 拆开事实与金额校验，删除固定饭量加餐目标作为生产搜索/推荐条件；保留明确硬约束核对。
- [ ] 实现纯在线生产入口，测试 mode 仍用注入的 fake provider，不使用生产快照回退。
- [ ] 运行专项及相关原有测试，保留 GREEN；独立复审。

### Task 2: 官方核价、订单与支付

**Files:** 新建 `checkout_service.py`、`order_repository.py`、`checkout_provider.py`；主代理修改 schemas/routes/PlanStore 兼容字段；新增报价和订单测试。

- [ ] 编写失败测试：官方价格整数分不二次换算，费用未知保留未知，同款多规格行价不伪分摊。
- [ ] 编写失败测试：价格/取餐方式变化不下单，官方单据字段正确，只有官方 HTTPS 支付 URL 可返回。
- [ ] 编写失败测试：同键同参只一次上游创建、同键异参冲突、聊天清方案不影响已存订单重放。
- [ ] 编写失败测试：提交超时、响应解码失败、进程重启遗留 submitting 均结果未知且不重试。
- [ ] 运行 RED；实现官方工具参数映射、SQLite 唯一键/事务和订单状态机。
- [ ] 接入 `/orders` 真实创建与 `GET /orders/{order_id}` 查询，移除模拟订单成功分支。
- [ ] 运行 GREEN 和相关集成测试；独立复审。

### Task 3: 在线前端、支付导航与视觉

**Files:** `App.tsx`、`lib/api.ts`、`lib/types.ts`、PlanCards/KitchenFeedback/HeroScene/OpeningCopy、样式、对应测试、README。

- [ ] 编写失败测试：无离线入口/静态成功、启动直接连接在线 API、网络错误显示重试。
- [ ] 编写失败测试：有效待支付结果进行当前页导航，双击单请求，报价变化等待再次点击。
- [ ] 编写失败测试：无效 URL、迟到响应、订单未知、支付返回不冒充成功。
- [ ] 运行 RED；替换类型和 API，只提交服务端方案及报价标识；支付请求同步锁、稳定幂等键与恢复链接。
- [ ] 移除生产 fixture 和本地模板导入；把仍需的 fixture 移到测试工具。
- [ ] 用系统中文字体及 16/14/12px 层级、深棕说明、达标红按钮和统一字重；替换设计中列出的冗余文案。
- [ ] 保留厨房资产/动画和键盘焦点行为；运行专项测试与 build，保留 GREEN；独立复审。

### Task 4: 整合与验收

**Files:** 共享 routes/contracts、纯在线端到端测试、项目 README、独立验证目录及回滚脚本。

- [ ] 并行完成后核对接口字段与错误码，更新因产品行为替换而过时的测试；保留旧算法作为非生产单元的直接测试。
- [ ] 完整后端 `tests backend/tests` 与前端 `npm test`、构建全部通过；失败逐一处理，不只跑新增测试。
- [ ] 用真实 OpenAI + MCP 读取与算价验证模型方案，确认 `llm_skill / live_mcp / live_quote`，不自动创建真实订单。
- [ ] 浏览器实测 320/390/800/1440px、200% 缩放、键盘、减少动态、无横向溢出；保存截图。
- [ ] 导出精确差异、原始/修改哈希及 VERIFICATION.txt；在独立副本恢复并运行原始测试，当前工作区保持新代码。
- [ ] 最终整体验证与独立审查；报告通过结果和实际未执行的真实创建/付款，不虚构支付成功。

## 执行方式

推荐在当前会话由主代理维护共享契约，三个实施者按文件所有权并行实施，完成后逐项复审、整合；不创建新的用户任务或工作树，不覆盖现有未提交内容。

## 基线

- 实际文件备份：`frontend/verification/llm-online-v13/before`，共 133 文件，凭据排除。
- 后端：234 passed in 8.37s，exit 0。
- 前端：`baseline-frontend.json` 已生成，完成后读取统计。
- 模型连接：HTTP 200，`gpt-6-luna` 可用。

## 后续指令与验收状态

- 用户要求改用订阅并已登录：默认 codex_subscription，CODEX_MODEL 留空；无 API 自动降级。
- 模型配餐、支付后端、前端均已实施；独立审查的重要问题已修复并复审。
- 真实订阅 + 实时 MCP + 官方核价已通过；支付创建由页面点击触发，自动验收不创建订单。
- 完整测试、视觉与独立副本回滚的最终证据见 frontend/verification/llm-online-v13/VERIFICATION.txt。
