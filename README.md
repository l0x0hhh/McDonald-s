# 麦门小厨房

按人数、预算和忌口，从当前门店菜单里搭配一餐。模型读取项目 Skill 选餐，后端核对商品与选配、请求官方报价；选好后前往官方页面付款。

## 本地启动

需要 Python 和 Node.js。启动后端：

```powershell
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 18000
```

另开终端启动前端：

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

打开 [小厨房](http://127.0.0.1:5173/)，点击右上角 **设置**，填写 **DeepSeek API Key** 和 **麦当劳 MCP Key**。模型固定 DeepSeek（deepseek-flash），无需配置模型地址；MCP 默认官方服务。已有 Key 可留空保留，保存后即时生效。

Key 仅保存在服务端本机 `backend/data/settings.local.json`（Git 忽略），不回显、不写浏览器存储。环境变量可使用 `DEEPSEEK_API_KEY`、`MCD_MCP_TOKEN`，本地保存设置优先。模型不再使用 Codex 订阅或 OpenAI Key。

前端默认同源 `/api`，Vite 转发至后端18000。局域网可运行 `npm.cmd run dev -- --host 0.0.0.0`，打开本机局域网IP的5173端口；后端仍只监听回环。静态部署需配置 `/api` 反向代理。不使用8000。

## 配餐与付款

- `backend/skills/meal-planner/SKILL.md` 是后端实际加载的配餐指令，包含版本和哈希。
- 生产链只读实时菜单，使用模型候选；不运行旧搜索、不固定三套排名、不回退离线样例。
- 模型固定 DeepSeek（deepseek-flash），只用设置页保存的 Key 或服务端 DEEPSEEK_API_KEY；不回退订阅或其他 API。
- 保存 Key 与下单互斥；设置更新后旧方案失效，需要重新配餐。
- 商品行价标为菜单参考；总价来自 `calculate-price`。缺失的费用保持未知，不填零，不给同款不同选配伪分摊官方行价。
- 选择取餐方式后点击“就选这份”，后端再次核价，再创建待支付订单。价格、需求或方案已变化时重新确认，不直接创建。
- 页面只跳转官方返回并通过验证的 HTTPS 支付链接；付款由用户在官方页面完成。
- 订单持久化在本机 SQLite，同一请求不重复创建；提交结果未知时先核实，不自动再下单。只有官方订单状态确认后才显示已付款。

## 界面

React + TypeScript + Vite + Motion。保留红黄奶油色厨房、立体按钮、厨师待机与弹簧动效；采用系统中文字体和更清晰的字号，支持键盘、移动布局与减少动态效果。

## 验证

```powershell
python backend/tests/run_settings_v15.py backend/tests tests -q --basetemp=.qa_tmp/final
cd frontend
npm.cmd test
npm.cmd run build
```

测试显式注入假模型和假上游，生产代码不导入测试样例。自动联调只运行真实读取、模型与官方算价；不自动创建真实订单或付款。

本轮设计和实施计划位于 `docs/superpowers/`；测试、浏览器截图、变更哈希和独立副本回滚证据位于 `frontend/verification/settings-v15/`。更早团队材料保留在 `docs/`，旧版本的订阅、演示模式和模拟订单描述不代表本版行为。
