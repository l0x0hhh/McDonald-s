# 麦门小厨房 · 前端首版

React + TypeScript + Vite，Motion for React 弹簧动效，Lucide 图标。
视觉采用黄红、奶油底色、圆润玩具厨房、柜台与订单小纸条；不是游戏画面的直接复制。

## 启动

在 `G:\program\McDonald-s\frontend` 执行：

```powershell
npm.cmd ci
npm.cmd run dev
```

浏览器打开 http://127.0.0.1:5173/ 。Windows 使用 `npm.cmd` 可避免本机 `npm.ps1` 执行策略影响。

Windows 也可双击 `start-dev.cmd`：已有本项目前端服务时直接复用，否则启动 5173 的 Vite；手动启动后请保留该终端窗口。`start-dev.cmd --check` 只检查连通性，不启动服务。这个入口不启动后端，也不占用 8000。

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run preview
```

当前依赖版本以 package-lock.json 为准；Node.js 使用本轮实测的 v24.15.0。

## 两种数据模式

- **离线样例**：默认模式，无需后端。读取仓库既有 JSON，明确标注历史快照和 demo_sample。
  - 成功样例：广州今日广场餐厅、6 人、总预算 150 元、2 人不辣。
  - 预算无解样例：同条件、总预算改为 30 元。
  - 其他门店和需求展示“无可用离线样例”，不等同于真实点餐无解，不重新编价格或忽略约束。
  - 模拟结算只接受本轮已生成的样例 plan_id，金额取自所选方案，30 分钟失效。
- **连接后端**：直接调用现有 FastAPI。默认地址 http://127.0.0.1:18000 。可参考 .env.example 设置 `VITE_API_BASE_URL`，改后重启 Vite。
  - 前端不保存 MCP Token 或模型密钥。
  - API 模式不意味着在线 MCP：后端也可能处于 demo/auto 快照模式，以响应 data_source 为准。
  - 20 秒请求超时覆盖 fetch 和 JSON body；订单不自动重试。

本轮联调在仓库根目录以 `DATA_MODE=demo` 启动原有后端；当前 .venv 尚无 FastAPI/pytest，实际使用已有系统 Python 环境，未改变后端依赖文件。

## 已支持

- 城市/关键词选店、切换门店、来源和营业状态。
- 固定句式样例/后端需求解析、可编辑人数/预算口径/不辣人数、快捷样例。
- 动态方案卡、真实字段金额与餐份、检查/理由、图片失败占位、单品营养折叠查看。
- 确认明细、模拟结算、查看本次会话最近一次模拟计划（桌面导航）。
- 加载、无门店、无解、失败重试、降级与过期；改需求/换店即清旧方案，忽略过时请求。
- 弹簧按钮、卡片入场、弹层过渡；遵从系统减少动态效果；对话框焦点限制、Escape 和焦点恢复。

## 后端整合与前端能力边界

本轮将 `codex/ab-integration` 的 `39aea1d` 合入前端分支；它已经包含 A 的公共后端与 B 的正式 Skill。
「连接后端」模式的选店、基础需求、正式方案与模拟结算使用真实 HTTP 接口；数据仍可来自明确标注的演示快照。

后端现已提供无饮料、饭量、口味和部分需求草稿；这些扩展能力的前端控件仍待接入，并非后端缺少能力。
聊天返回无饮料或饭量/口味约束时，当前界面会明确暂停配餐，直到用户选择「使用基础需求」或重置，避免静默丢失条件。
聊天草稿会跨轮回传；编辑表单、换店、切换模式或重置后清除旧草稿。

`algorithm_source=skill` 与菜单的 `data_source` 分别展示；正式算法不代表实时菜单或收银报价。
会员/开卡对比、整餐营养汇总、活动与习惯回本仍依赖后续能力。未知辣度显示未知，balanced 展示「搭配多样」。
本轮测试、HTTP 冒烟、截图与隔离回滚记录位于 `verification/backend-integration-v10/`。

## 文件结构

```text
frontend/
  src/App.tsx                 页面、需求状态、对话框与流程
  src/components/PlanCards.tsx 方案与商品展示
  src/lib/types.ts            当前接口类型
  src/lib/api.ts              HTTP / 离线样例适配
  src/lib/demo-chat.ts        明示的固定句式预览
  src/styles.css             黄红厨房视觉与响应式布局
  public/images/             已落盘的原创生成素材
  tests/                     数据层与用户流程回归测试
  verification/              测试结果、差异、截图与隔离回滚验证
```

## 素材与参考

- 厨房插画由内置 image_gen 生成，已复制到 public/images/kitchen-hero.png；生成提示词见 verification/image-prompt.txt。
- 动效参考官方文档：https://motion.dev/docs/react-transitions 、https://motion.dev/docs/react-motion-config 。
- 图片为本地资源；Google Fonts 和餐品远程图片失败时分别回退到系统中文字体和商品图标。
- 完整检查结果见 verification/VERIFICATION.txt。ROLLBACK.sh 只还原指定隔离副本的 App 入口，不操作后端、不删除当前可用前端。

## 游戏质感 V2

- 主按钮采用高光面、厚底座与下沉反馈；方案卡采用浮雕边框、角铆钉、抬升餐品与错峰弹入。
- 确认弹窗采用奶油色任务板、把手与双层底座，弹出时带小幅旋转和明显回弹。
- 动效集中在 src/lib/motion-presets.ts：按钮/卡片 spring 为 stiffness 360、damping 17、mass 0.9；弹窗为 400 / 20 / 0.95。
- 保留减少动态效果分支，异步禁用按钮复位；320px 和 390px 小屏已检查。业务接口、金额与模拟结算语义不变。
- 本轮差异、49 项测试、截图与隔离回滚记录见 verification/game-feel-v2/VERIFICATION.txt。
- 测试仅收集根 tests/，避免将 verification/ 的隔离回滚副本重复计入。

## 首页待机与对齐 V3

- 保留原厨房插画；小厨师与食物所在的装饰画层以 5.8 秒周期轻微起伏（3px / 0.18°），叠加食物蒸汽和低频微光。
- 动画仅改变装饰的 transform/opacity，视觉层不接收指针；CTA、文字、表单与布局盒子不做待机位移。
- 支持手动暂停；画面离屏、页面隐藏、弹窗打开时暂停；实时响应系统减少动态效果设置。
- 首页和工作台共享列宽/间距，桌面断点为 430/26、380/20、340/16，手机保持单列；头部、正文和页脚共用左右边线。
- 代码位于 src/components/HeroScene.tsx / hero-scene.css，布局规则位于 styles.css 的 V3 shared rails 段。
- 本轮测试、截图、差异与隔离回滚证据见 verification/hero-idle-v3/VERIFICATION.txt。


## 双面板对齐 V4 与本地端口

- 桌面左右两栏现在是同起点、同底边的等高任务板；右侧标题使用与左侧相同的面板标题栏，三份方案在右栏内部滚动。
- 方案列表可聚焦并使用键盘滚动，保留滚动提示、原生滚动条与动画留白；手机恢复自然高度、整页滚动。
- 前端继续监听 127.0.0.1:5173；已停止此前的 8000 调试服务。后端默认地址及启动示例统一为 127.0.0.1:18000，本轮未启动新后端。
- 本轮对齐实测、端口检查、测试、差异与隔离回滚记录见 verification/panel-alignment-v4/VERIFICATION.txt。
