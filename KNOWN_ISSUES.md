# cx2118 Script Weaver v10 — 已知问题与修复记录

本文档记录项目中发现并修复的关键 Bug，供 AI 和开发者快速了解项目历史问题。

---

## 🔥 致命级 Bug（已修复）

### 1. 前端所有按钮失效（SyntaxError）

**根因**: `pmTerminalLog('thought')` 和 `agent_reasoning` 事件处理中，`innerHTML` 包含三层嵌套引号的 `onclick` 属性：
```
JS字符串 → HTML onclick属性 → 内部JS代码（含三元运算符和Unicode字符 ▸/▾）
```
这种 `'...'?\'block\':\'none\';...\'none\'?\'▸\':\'▾\'` 模式导致浏览器 JS 解析器报 `SyntaxError: Invalid or unexpected token`。由于错误在 `<script>` 标签内，**整个脚本无法解析**，所有函数未定义，所有 onclick 失效。

**修复**: 将两处 `innerHTML` + `onclick` 替换为 `createElement` + `addEventListener`，完全避免引号嵌套。

**涉及文件**: `index.html` — `pmTerminalLog()` thought 分支 + `handleEvent()` agent_reasoning 分支

---

### 2. DeepSeek streaming 丢失所有换行符（两层问题）

**层1 — `.strip()` 吃掉换行 token**:
`main.py:193` 的 `stream_chat()` 对每个 streaming token 做 `.strip()`。DeepSeek 将 `\n` 作为独立 token 发送，`.strip()` 变空字符串后被过滤。

**层2 — 字面 `\n` 不是真正换行**:
即使 token 保留，DeepSeek 输出的 `\n` 是两个字符（`\`+`n`），不是 0x0A 换行符。需在 `full_response` 拼接后做 `.replace('\\n', '\n')`。

**修复**: 
1. 移除 `stream_chat()` 中的 `.strip()`
2. `full_response` 拼接后和发送前两处做字面转义

---

### 3. 工具调用解析器参数名连写

**根因**: DeepSeek 输出 `write_file` 工具调用时，将工具名和参数名连写：
```
write_filefilename=test.pycontent=import math\nprint(1+1)
```
解析器用 `\w+` 匹配工具名，贪婪匹配到 `write_filefilename`（整个词），导致工具名错误、参数为空。

**修复**:
1. 工具名识别：已知工具名前缀匹配，拆分 `write_filefilename` → `write_file` + `filename=...`
2. 参数名识别：用已知参数名正则在值中插入换行分隔，`filename=test.pycontent=` → `filename=test.py\ncontent=`
3. 事后转换：值中的字面 `\n`（两个字符 `\`+`n`）转为真实换行符

**涉及文件**: `pm_agent.py` — `_parse_tool_calls()`

---

### 4. body::after 噪声覆盖层阻断所有点击

**根因**: `body::after` 伪元素以 `z-index:9999; position:fixed; inset:0` 覆盖整个视口。虽然有 `pointer-events:none`，在 WebKit/Safari 上可能失效。

**修复**: `display:none` 完全禁用噪声覆盖层。

**涉及文件**: `index.html` — CSS `body::after`

---

### 5. CSS 选择器拼写错误（no-glass 模式）

**根因**: `.body.no-glass button` 使用了 `.body`（class选择器），应为 `body.no-glass button`（tag选择器）。导致 no-glass 模式下按钮无 hover/active 反馈。

**修复**: 修正选择器。

**涉及文件**: `index.html` — no-glass 模式 CSS

---

## ⚠️ 中等级 Bug（已修复）

### 6. Plan 模式双输入框

**根因**: `switchMode('pm_agent')` 同时显示 `mainInputBar`（底部）和 `pmInputBar`（pmChatArea内），用户看到两个聊天输入框。

**修复**: Plan 模式隐藏 `mainInputBar`，只使用 pmChatArea 内的输入框。

**涉及文件**: `index.html` — `switchMode()` pm_agent 分支

---

### 7. 思考过程逐条显示而非合并

**根因**: 每次 `agent_reasoning` SSE 事件都创建新的折叠元素，产生多个独立的 "Thought" 块。

**修复**: 累积所有 reasoning 内容到单一 `#pmReasonBlock` 元素。

**涉及文件**: `index.html` — `handleEvent()` agent_reasoning 分支

---

### 8. sendMsg 在 Plan 模式下消息丢失

**根因**: `sendMsg()` 在 Plan 模式下创建新项目后递归调用自身，但 `ti.value` 已被清空，递归调用收到空消息直接 return。

**修复**: Plan 模式直接委托给 `sendPmMsg()`，消除重复逻辑。`sendPmMsg` 创建项目后回填消息再重发。

**涉及文件**: `index.html` — `sendMsg()` + `sendPmMsg()`

---

### 9. switchMode planning 阶段视图映射错误

**根因**: `switchMode('pipeline')` 中 `showV($('vCode')&&phase==='coding'?'vCode':'vReq')` — planning 阶段错误显示 vReq 而非 vPlan。

**修复**: 使用阶段-视图映射表。

**涉及文件**: `index.html` — `switchMode()` pipeline 分支

---

### 10. search_config 未定义引用

**根因**: `main.py:2977` 使用了未定义变量 `search_config.provider`。

**修复**: 改为 `getattr(web_searcher, 'provider', 'unknown')`。

**涉及文件**: `main.py` — `api_web_search()`

---

### 11. `_pmTerminal` 空指针

**根因**: `agent_reasoning` 事件处理中直接访问 `_pmTerminal.appendChild()`，如果 `_pmTerminal` 为 null 则崩溃。

**修复**: 添加 null 检查和 fallback 获取。

**涉及文件**: `index.html` — `handleEvent()` agent_reasoning 分支

---

### 12. pmStreamEnd onclick 语法错误

**根因**: `pmStreamEnd()` 中 innerHTML 的 onclick 属性内三元运算符用了 `:` 代替 `?`，导致点击折叠时 JS 报错。

**修复**: 改用 `createElement` + `addEventListener` 避免 innerHTML 引号问题。

**涉及文件**: `index.html` — `pmStreamEnd()`

---

## 📋 前端显示修复

### 13. 响应消息换行和 HTML 标签清理

**修复**: 添加 `cleanAI()` 全局函数：
- 去除 `<think>`/`<reasoning>`/`<thinking>`/`<thought>`/`[thinking]` 标签
- `<br>` → `\n`（保留换行）
- 去除常用 HTML 标签（div/span/p/code/pre 等）
- `&nbsp;` → 空格
- 字面 `\n` → 真实换行
- `\r\n` → `\n`
- 多余空行压缩

**涉及文件**: `index.html` — `cleanAI()` + `pmTerminalLog()` response 分支

### 14. 响应消息气泡样式

**修复**: `pmTerminalLog('response')` 使用圆角卡片 + PM标签 + `white-space:pre-wrap`，移除 `animation:msgIn` + `opacity:0`（在 no-glass 模式下 animation-duration:0s 导致元素不可见）。

**涉及文件**: `index.html` — `pmTerminalLog()` response 分支

---

## 🔧 后端修复

### 15. 资源清理

**修复**: 添加 `window.beforeunload` 事件清理 `pollTimer`、EventSource、Workflow SSE。

**涉及文件**: `index.html` — 初始化代码

---

## ⚙️ 项目架构速览

- **前端**: `index.html` — 单文件 SPA，三种模式（Build/Compose/Plan）
- **后端**: `main.py` — FastAPI 服务，SSE 流式推送
- **PM Agent**: `pm_agent.py` — Plan 模式核心，工具调用循环（最大16轮）
- **LLM Client**: `main.py:LLMClient` — DeepSeek/OpenAI 兼容流式调用
- **工具调用格式**: `<tool_call>tool_name\nkey=value\n</tool_call>`，但 DeepSeek 经常连写
- **SSE 事件类型**: Build 用 `pm_thinking/token/response/done`，Plan 用 `agent_token/response/reasoning/done/error`
- **DeepSeek 特性**: `reasoning_content` 字段存思考内容，`\n` 作为独立 token 流式发送
- **Bug 文档**: `KNOWN_ISSUES.md`（本文件），供 AI 快速了解历史问题
