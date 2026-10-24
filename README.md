<!-- 新增说明：提供项目入口、赛事约束与三人协作指引；当前为方案阶段，未声明业务功能已实现。 -->
# 麦门 AI 点餐助手

面向多人聚餐的 AI 点餐助手：根据人数、预算和饮食限制，结合麦当劳 MCP 数据生成可解释的餐品方案。

**当前状态：A 的后端已可运行，B 的正式算法与 C 的前端尚未接入。**

已实现并实测（2026-10-24）：
- 后端四个接口（选店 / 需求解析 / 方案生成 / 模拟结算）全部跑通，使用 FastAPI。
- **真实麦当劳 MCP 集成已验证**：门店、菜单、营养数据均来自真实调用，非静态编造。
- 数据适配含辣度本地规则（MCP 无辣度字段）、随单购麦金卡价格陷阱标记。
- `live` / `demo` / `auto` 三种运行模式；真实数据失败时降级到本地快照并明示原因。
- 模拟结算带 `is_demo=true` 标识，金额由服务端方案重建，不信前端自报。

尚未实现：
- **B 的正式组合与优惠算法**：当前 `/plans` 走 `services/sample_planner.py` 演示样例，
  响应 `suggestions` 会标注 `demo_sample`，不冒充正式算法。
- **B 的 LLM 需求提取**：当前为内置规则解析（`parse_source=rule`）。
- **C 的前端页面**：仓库暂无 `frontend/`。
- 真实支付、真实下单、营养精确计算均不在本次范围。

B/C 对接方式见[接口与模块契约](./docs/接口与模块契约.md)，
MCP 实测结论见[能力核实报告](./docs/mcp能力核实报告.md)。
下列未标注完成的功能仍为开发计划，不能把本仓库当作已完成参赛作品。

## 项目方案

请先阅读[项目执行方案 V0.4](./麦门AI点餐助手-项目初步方案.md)，其中包含功能范围、技术架构、三人分工、接口样例、验收场景和提交清单。

开发前再阅读[团队协作流程](./团队协作流程.md)：包含功能分支、PR 合并、冲突处理和 WorkBuddy 记录汇总的具体操作。

计划主线：选店 → 输入需求 → 生成差异化方案 → 展示数量与优惠明细 → 模拟结算。

## 赛事与时间

参加 WaytoAGI「1024 超能 Coding 周末」用餐赛道，目标为赛道前三。时间均为北京时间：

- 2026-10-25 14:00：团队冻结新增功能。
- 2026-10-25 15:00：赛事封板，停止开发。
- 2026-10-25 16:00：赛事站提交截止。

用餐赛道要求作品真实使用麦当劳 MCP。争取赛道前三需使用 WorkBuddy 开发并附 workbuddy.md，内容应来自真实开发对话，提交前脱敏。模拟支付和离线快照属于本团队的演示设计，不能冒充真实付款或真实 MCP 调用。

参考：[选手手册](https://waytoagi.feishu.cn/wiki/Ht49w6i3kipT0Vk61H3cmUxtnJg)、[赛事介绍](https://waytoagi.feishu.cn/wiki/AYIownRz0ia1Auk41n3cbsC9nDb)。本次仅发布文档，尚未代团队报名或提交赛事作品。

## 三人协作

| 角色 | 工作边界 | 首个交付 |
|---|---|---|
| A：后端与集成 | FastAPI、公共模型、MCP 适配、配置、主流程和合并 | 统一接口样例、演示 API、真实 MCP 能力清单 |
| B：AI 与决策 | 需求提取、餐份约束、候选生成、优惠与排序 | 输入需求和菜单、输出方案的 Python 模块 |
| C：前端与体验 | React 页面、需求编辑、方案明细、状态反馈和模拟结算 | 按相同接口样例完成可点击流程 |

A 维护公共模型与入口，B 的算法直接在后端进程内调用，C 集中管理前端 API 请求。跨模块变更先同步，尽早合并小闭环。具体人员由团队认领。

拟用技术：Vite + React + TypeScript、Python 3.11 + FastAPI、MCP Python SDK、一个选定的 LLM 供应商、JSON 快照；持久化有需要再增加 SQLite。

## 运行后端（已实测）

```bash
# 仓库根目录
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt   # Windows
# .venv/bin/python -m pip install -r backend/requirements.txt         # macOS/Linux

# 复制 backend/.env.example 为 backend/.env，填入 MCD_MCP_URL / MCD_MCP_TOKEN
# 没有凭据时把 DATA_MODE 设为 demo，即可用本地快照跑通全流程

.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8000 --reload
```

- 接口调试页：`http://127.0.0.1:8000/docs`；健康检查：`GET /healthz`。
- 本地验证环境：Python 3.13 + fastapi 0.162 + httpx 0.28。
- 演示快照刷新：`.venv/Scripts/python.exe -m backend.scripts.build_snapshot --store 1400011`。
  快照含采集时间与 traceId，是脱敏后的真实调用证据。
- 前端启动方式待 C 补充。

## 开发与交付待办

- [x] 建立后端代码与依赖清单，实测安装、配置和启动步骤。
- [x] 真实 MCP 读取结果进入方案生成；快照记录脱敏后的调用时间与 traceId。
- [ ] B 接入 `backend/skill` 正式算法（组合、优惠、排序、去重），替换演示样例。
- [ ] B 接入 `backend/llm.py` 需求提取，替换规则兜底。
- [ ] C 完成前端可点击流程，覆盖加载、失败、无解、演示标识状态。
- [ ] 支持 4 人、人均 30 元，以及 6 人、总预算 150 元、2 人不吃辣等场景。
  （后端接口已验证这两个场景，待前端与正式算法对齐验收）
- [ ] 优惠不可叠加与开卡对比的正式口径。
- [ ] 从真实 WorkBuddy 开发对话整理 workbuddy.md。
- [ ] 补齐使用说明、实际录屏、海报和开源/模板来源。
- [ ] 由团队成员在 16:00 前到赛事站提交，确认回执。

## 记录与来源

本轮项目文档由团队讨论及 Codex 辅助整理；业务实现计划使用 WorkBuddy，后续按实际过程记录使用工具。麦当劳 MCP Token、模型密钥、手机号和真实订单个人信息不得提交到公开仓库。

如后续决定同步参加麦当劳独立赛事，另行核实其 Skill 要求、声明文件和报名流程；这不改变本次 WaytoAGI 的提交时间。
