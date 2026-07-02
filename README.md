# cx2118 Script Weaver v9.3

AI 驱动的多智能体编码工具。输入需求，AI 自动完成从规划到编码的全流程。

开源 · 免费 · 一站式从想法到代码

---

## 功能介绍

### ⚡ Build 模式 — 全自动编码流水线

Build 是主战场。你只需要用自然语言描述需求，AI 自动完成整个编码流程：

```
你：做一个贪吃蛇游戏
AI：[PM 确认需求] → [Director 出方案] → [Python 编码] → [AI 审查] → [交付]
```

**工作流程：**

1. **PM 需求讨论** — AI 项目经理跟你确认需求细节
2. **Director 规划** — 自动分析需要 Python 还是 HTML，生成项目结构
3. **编程师编码** — Python 编程师 + HTML 编程师并行工作
4. **AI 代码审查** — 自动检测语法错误、逻辑问题，提出修复建议
5. **人工确认** — 你可以接受修复、跳过、或者让 AI 继续改

**特点：**
- 支持 Python / HTML / JavaScript
- 行级精准编辑（不是每次都重写整个文件）
- 语法检查（ast.parse）确保代码能跑
- AI 审查自动修复，也可以手动逐条处理
- 实时 diff 显示改了什么
- 文件自动保存到 workspace 文件夹

---

### 📋 Plan 模式 — 项目经理 + 工具调用

Plan 模式是一个独立的 AI Agent，它不只是聊天，还能直接操作文件：

```
你：帮我创建一个 Flask 项目
AI：[列出文件] → [创建 app.py] → [创建 requirements.txt] → [测试运行] → [完成]
```

**17 个内置工具：**

| 类别 | 工具 | 说明 |
|------|------|------|
| 查看 | `list_workspace` | 列出所有文件 |
| 查看 | `read_file` | 读取完整文件（带行号） |
| 查看 | `read_file_lines` | 读取指定行范围 |
| 查看 | `get_cwd` | 获取工作目录 |
| 创建 | `create_file` | 创建新文件 |
| 写入 | `write_file` | 覆盖写入整个文件 |
| 修改 | `patch_file` | 精确查找替换 |
| 追加 | `append_file` | 向文件末尾追加 |
| 重命名 | `rename_file` | 重命名文件 |
| 删除 | `delete_file` | 删除文件 |
| 执行 | `run_code` | 运行 Python 代码片段 |
| Git | `git_init` / `git_commit` / `git_log` / `git_rollback` | 版本管理 |
| 导入 | `import_zip` | 从 URL 导入项目 |
| 完成 | `done` | 任务完成总结 |

**特点：**
- 与 Build 共享同一个 workspace 文件夹
- 支持多项目管理（每个项目独立对话历史）
- 工具调用全程可视化（终端风格显示）
- 支持切换不同 AI 模型对比效果
- 文件修改前自动 Git 备份

---

### 🔗 Compose 模式 — 可视化工作流（规划中）

未来的可视化编排模式。用拖拽的方式搭建 AI 工作流：

```
[输入] → [LLM 分析] → [条件判断] → [代码生成] → [输出]
```

**规划功能：**
- 可视化节点编辑器，拖拽连线
- 支持 20+ 节点类型（LLM / 代码 / 文件 / HTTP / 条件 / 循环...）
- 节点包导入导出，社区共享工作流
- 一键执行，实时查看每个节点的执行状态
- 断点恢复，执行中断后可以从上次位置继续

**当前状态：** 基础框架已实现，后续持续完善。

---

## 优点

### 从想法到代码，一站式完成

传统流程：想需求 → 写文档 → 手写代码 → 调试 → 改 bug → 部署

Script Weaver：**说一句话 → AI 全搞定**

你只需要描述你想要什么，AI 自动完成需求分析、技术规划、编码、审查、修复的全流程。省掉 80% 的重复劳动。

### Build + Plan 双模式协作

- **Build** 负责从零搭建项目框架（大框）
- **Plan** 负责日常维护和修改（精细操作）
- 两者共享同一个 workspace 文件夹，数据实时同步

### 一站式修改

改代码不用来回切换工具：

- 在 Plan 模式里直接说「把 main.py 的函数名改一下」
- AI 自动读取文件 → 找到目标 → 精确修改 → 验证
- 全程在终端里看到每一步操作

### 开源免费

- MIT 开源协议，随便用
- 不依赖特定 AI 服务商，DeepSeek / OpenAI / SiliconFlow 都支持
- 所有代码都在本地运行，数据不上传
- 插件系统可扩展，社区共建

### 工具可视化

Plan 模式的每次工具调用都清晰可见：

```
▸ 📂 列出文件
  ◀ ✓ 14 个文件
▸ 📄 读取 main.py
  ◀ ✓ main.py (10 行)
▸ Git commit: 备份：修改前
  ◀ ✓ committed
▸ ✏️ 修改 main.py
  ◀ ✓ 已修改
✅ 完成
```

---

## 部署

### 手动部署

**环境要求：**
- Python 3.10+
- 一个 LLM API Key（DeepSeek / OpenAI / SiliconFlow）

**安装：**

```bash
# 克隆项目
git clone https://github.com/CX2118/CX2118-Script-Weaver
cd cx2118-script-weaver-v9

# 安装依赖
pip install fastapi uvicorn openai httpx

# 启动
python main.py
```

浏览器打开 `http://localhost:8880`，在设置里填入 API Key 即可使用。

### 一键部署（规划中）

未来计划支持：

- **Docker 一键部署**
  ```bash
  docker run -p 8880:8880 cx2118/script-weaver
  ```

- **docker-compose 部署**
  ```bash
  docker-compose up -d
  ```

- **云端一键部署**（GitHub Actions / Vercel / Railway）（已取消）
  - Fork 仓库 → 配置 API Key → 自动部署
  - 无需本地环境，浏览器直接使用

- **桌面应用打包（失败产品）**
  - PyInstaller 打包为独立可执行文件
  - 双击即用，不需要安装 Python

---

## 文件结构

```
cx2118-script-weaver-v9/
├── main.py              # 主程序，FastAPI 服务
├── llm_client.py        # LLM 统一客户端
├── pm_agent.py          # PM Agent（Plan 模式核心）
├── workflow_engine.py   # 工作流引擎
├── plugin_engine.py     # 插件系统
├── sandbox.py           # 代码沙箱
├── storage.py           # 数据持久化
├── multi_file_manager.py # 多文件管理
├── tool_manager.py      # 工具管理器
├── project_dispatch.py  # 项目调度器
├── breakpoint_recovery.py # 断点恢复
├── web_search.py        # 网络搜索
├── index.html           # 前端页面
├── config.json          # 配置文件
├── workspace/           # 共享工作区（Build 和 Plan 都在这里操作）
├── plugins/             # 插件目录
└── .workspace_backups/  # 工作区归档
```

## 快捷键

- `Tab` — 在三种模式间切换
- `Enter` — 发送消息

## 常见问题

**Q: 启动报错 `No module named 'xxx'`**
A: 装依赖。`pip install fastapi uvicorn openai httpx`

**Q: 对话报 `Failed to fetch`**
A: 服务没启动。重新跑 `python main.py`。

**Q: AI 不回话 / 卡住**
A: 检查 API Key 是否有效。点设置里的「测试连接」。

**Q: Plan 模式工具调用不执行**
A: 确保 AI 模型支持工具调用（推荐 deepseek-v4-flash）。

**Q: Build 和 Plan 的文件不同步**
A: 两者共享 workspace 文件夹，正常情况下自动同步。如不同步，刷新页面重试。

**Q: 为什么没有最新9.3**
A: 因为25号更新v9.3版本。

## 许可

MIT License
