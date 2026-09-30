<div align="center">

# Weix

### 让 AI 接入你的微信回复流程

本地数据库收消息 · 视觉模型定位 · 可配置的 AI 回复

[![Build](https://github.com/zqaini002/weix/actions/workflows/build.yml/badge.svg?branch=master)](https://github.com/zqaini002/weix/actions/workflows/build.yml)
[![Stars](https://img.shields.io/github/stars/zqaini002/weix?style=flat&color=14b8a6)](https://github.com/zqaini002/weix/stargazers)
[![License](https://img.shields.io/badge/license-MIT-blue)](#license)

**[下载测试版](https://github.com/zqaini002/weix/releases/tag/nightly) · [全部版本](https://github.com/zqaini002/weix/releases) · [快速开始](#快速开始) · [反馈问题](https://github.com/zqaini002/weix/issues)**

</div>

## 下载与版本

| 平台 | 自动构建下载 | 使用方式 |
| --- | --- | --- |
| Windows | [Weix-Windows.zip](https://github.com/zqaini002/weix/releases/download/nightly/Weix-Windows.zip) | 解压完整目录，以管理员权限运行 `Weix.exe`，保留 `_internal` 目录 |
| macOS | [Weix-macOS.dmg](https://github.com/zqaini002/weix/releases/download/nightly/Weix-macOS.dmg) | 配置本机权限；微信界面自动回复仍待 Mac 实机验收 |

`master` 构建成功后自动更新 **Releases → 最新测试版**；`v*` 标签构建成功后发布版本 Release。首次构建发布完成后下载链接生效。构建包的管理页面为 **http://127.0.0.1:8000**。

配置模板随程序提供，API key 和白名单由你在本机填写。发布包不包含开发者的密钥或聊天数据。GUI 自动化需要可操作的微信窗口，频率限制和校验机制不能保证账号不受平台限制。

## 已验证的进展

| 项目 | 当前结果（2026-09-30） |
| --- | --- |
| Windows 微信 | 4.1.15.13：本机数据库密钥恢复、数据库监听、视觉定位和发送回读已验证 |
| 切换会话测试 | 两个私聊交替三轮，6 条消息均回读到正确会话 |
| 后端测试 | 本机测试记录：166 项通过，4 项跳过 |
| macOS | 同步代码与模拟测试已具备；界面流程待实机验证 |

## 核心原理

- **收消息**：直接读取微信本地 SQLite 数据库（纯文件 I/O，微信进程无感知）
- **AI 回复**：LangChain 编排大模型，支持多轮对话、工具调用、意图识别
- **发消息**：两个平台均采用 GUI 模拟操作，不注入、不 Hook，与真人操作无异
  - Windows：pyautogui 模拟鼠标点击 + 右键粘贴
  - macOS：AppleScript 模拟键盘输入
- **可视化管理**：Vue3 Web 后台，配置 AI、规则、模板，开箱即用

## 功能

### 核心功能：全 AI 自动回复
- **智能对话**：接入 DeepSeek / OpenAI / 硅基流动等大模型，像真人一样聊天
- **多轮记忆**：记住上下文，长期记忆支持 90 天回溯
- **工具调用**：天气查询、地图导航、搜索、计算等，AI 自动调用工具
- **意图识别**：点单、投诉、咨询等意图自动触发对应工作流
- **人设定制**：自定义 System Prompt，设定回复风格和角色
- **本人 Skill**：AI 分析你的历史聊天记录，自动学习你的语气、风格和习惯，替你以假乱真地回复

### 增强功能
- 关键词 / 正则规则兜底（AI 没匹配到时走规则）
- 工作流引擎（陪玩点单流程：填单 → 确认 → 转发接单群 → 分配）
- 消息模板（文本 / 卡片 / 表单 / 列表，支持变量替换）
- 转发规则（关键词 / 工作流事件触发，多目标群转发）
- 统计分析（发言排行 / 时段热力图 / TF-IDF 关键词 / AI 摘要）
- 定时任务（日报 / 周报 / 数据清理 / 健康检查）
- 防封号策略（频率控制 / 行为模拟 / 熔断保护）

## 系统架构

```
微信客户端 ──(只读)──▶ 数据库解密层 ──▶ 消息监听器 ──▶ AI Agent（核心）
                                                         │
                                               LangChain + 大模型
                                                         │
                                          ┌──────────────┼──────────────┐
                                          ▼              ▼              ▼
                                      规则引擎       工作流引擎      工具调用
                                          │              │              │
                                          └──────────────┼──────────────┘
                                                         ▼
                                                  消息发送层（GUI 模拟）
                                              ┌─────────┴─────────┐
                                              ▼                   ▼
                                    Windows (pyautogui)   macOS (AppleScript)
```

## 平台支持

| 维度 | Windows | macOS |
|------|---------|-------|
| 微信版本 | PC 微信 4.1.15.13：本机密钥恢复、数据库收消息、视觉发送与交替会话发送已验证 | Mac 微信 4.x (App Store)，视觉路径待实机验证 |
| DB 路径 | `Documents/xwechat_files/<账号>/db_storage/`，兼容旧 `WeChat Files/<账号>/Msg/` | `~/Library/Containers/com.tencent.xinWeChat/...` |
| 密钥提取 | `ReadProcessMemory` (Win32 API) | `mach_vm_read_overwrite` (Mach VM) |
| 消息发送 | pyautogui 模拟鼠标点击 + 右键粘贴 | AppleScript 模拟键盘输入 |
| 发送校验 | 前台与标题核对；发送后按会话 ID 回读数据库 | 标题核对；待实机验收 |
| 管理员权限 | 需要 | 需要 |

## 技术栈

- 后端：FastAPI + SQLAlchemy (async) + aiosqlite
- AI：LangChain 0.3+ + LangGraph
- 前端：Vue 3 + Element Plus + ECharts + Pinia
- 定时：APScheduler
- 数据库解密：pycryptodome (SQLCipher 4)

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/zqaini002/weix.git
cd weix
```

### 2. 创建本地配置文件

```bash
# 复制配置模板
cp config/config.example.yaml config/config.yaml

# 复制环境变量模板
cp .env.example .env
```

然后编辑这两个文件，填入你的 API Key 等信息。

### macOS

```bash
# 3. 环境初始化
bash scripts/setup.sh

# 4. 授予辅助功能权限
# 系统偏好设置 → 隐私与安全性 → 辅助功能 → 添加终端

# 5. 启动（首次提取密钥需 sudo）
sudo bash scripts/start.sh
```

### Windows

```cmd
REM 3. 环境初始化
scripts\setup.bat

REM 4. 以管理员权限启动
scripts\start.bat
```

启动时会验证**当前电脑**的数据库密钥；缓存失效时重新提取并验证。`data/all_keys.json` 属于本机数据，不应从另一台电脑复制。收消息始终依赖数据库；消息库或联系人库无法打开时，自动回复保持停发。旧配置中的 `monitor.source: vision` 会自动按数据库模式处理。

发送定位需要在本机 `.env` 中设置 `DEEPSEEK_API_KEY`，例如 `DEEPSEEK_API_KEY=...`；程序会读取它，进程环境变量优先。如 Windows 自动提取数据库密钥失败，可在本机 `.env` 设置 `WEIX_WECHAT_DB_KEY`（消息库密钥）和 `WEIX_WECHAT_CONTACT_DB_KEY`（联系人库密钥），均为 64 位十六进制；密钥仍须通过本机数据库验证。不要提交 `.env`、`config/config.yaml` 或 `data/all_keys.json`。Windows 需能截取并操作微信窗口；macOS 需给运行程序的终端或应用授予“辅助功能”和“屏幕录制”权限。两平台发送前都会重新搜索联系人、让视觉模型定位联系人行，并再次识别当前聊天标题；任何一步不确定都会停发。

Web 私聊白名单为空时自动回复停发，保存后会尝试重建数据库监听。启动日志显示 `source=database`；缺少 API key、白名单为空或数据库不可用时，日志会给出停用原因。Windows 已实测数据库新消息触发 AI 生成回复，以及视觉定位和发送回读；2026-09-30 两个私聊交替三轮、六条消息全部回读到正确会话，用户反馈效果正常。macOS 已有模拟测试，仍需在 Mac 上完成界面验证。

Windows 发送器会在截图、视觉判断返回及鼠标操作前检查微信是否处于前台；激活失败或被其他窗口抢走焦点时停止操作，并记录具体原因。自动回复操作期间请避免切换窗口，以便完成搜索、标题确认和发送。

Windows 自动回复发送后会按数据库会话 ID 回读确认消息；视觉模型只接收搜索结果和聊天标题。独立实机测试脚本为 `backend/live_reply_probe_windows.py`：默认仅检查数据库联系人，`--allow-vision` 启用定位检查，另加 `--send` 才发送测试消息。`--alternate-with` 可指定第二个联系人，执行三轮交替测试；任一步失败立即停止，不自动重发。联系人可用昵称或备注指定，脚本会解析为唯一会话 ID 和本机备注，报告保存在 `data/live-reply-probe.json`。

Windows 微信 4.1.15.13 已适配 `Config.Cipher` 内存缓存：只读恢复逐库密钥，并通过本机 SQLCipher 页校验后缓存。2026-09-30 本机实测约 4.4 秒提取并验证 20 个数据库密钥，成功打开联系人库和消息库；新版本或其他电脑仍以实际验证结果为准。此 Windows 对象布局不用于 macOS；Mac 保留自己的本机提取与验证路径。

### 实机测试与 EXE

本次 Windows 产物为 `dist/Weix415Reply/Weix415Reply.exe`，使用时保留旁边的 `_internal` 目录，先关闭旧程序再启动。EXE 与本地配置均不上传 Git。Windows 后端测试结果为 166 项通过、4 项跳过；这不替代另一台电脑或 Mac 的实机验证。

以下脚本默认只核对联系人。加入 `--allow-vision` 后会把搜索结果和聊天标题截图发送至 DeepSeek；只有另加 `--send` 才发送三轮测试消息，请先取得测试对象的授权。

```powershell
.venv/Scripts/python.exe backend/live_reply_probe_windows.py `
  --receiver "测试账号A" --alternate-with "测试账号B" `
  --package-dir dist/Weix415Reply --keys data/all_keys.json
```

原始测试报告、日志、截图和数据库密钥保存在本机 `data/`，不作为公开文档提交。发现历史泄露时参考 [Git 历史敏感数据清理](docs/security-history-cleanup.md)；仅删除当前文件或添加 `.gitignore` 不会清除旧提交。

## 管理后台

访问 http://localhost:5173，默认用户名/密码在 `config/config.yaml` 中配置（从 `config/config.example.yaml` 复制后修改）。

- **仪表盘**：在线状态、消息数、活跃群聊、订单数
- **统计报告**：发言排行、时段分布、关键词、AI 摘要
- **消息日志**：历史消息查询与详情
- **聊天配置**：群聊白名单、私聊权限、回复模式
- **自动回复规则**：关键词/正则/意图规则管理
- **消息模板**：文本/卡片/表单/列表模板编辑器
- **工作流配置**：状态机定义（默认含陪玩点单流程）
- **转发规则**：触发条件 + 目标群配置
- **AI 配置**：Provider、API Key、模型、System Prompt
- **本人 Skill**：AI 分析你的聊天记录，自动生成你的语气人设、自我记忆、私聊/群聊 Prompt
- **定时任务**：日报/周报/健康检查/数据清理管理
- **系统配置**：日志级别、数据保留、异常告警、备份恢复

## 配置文件

主配置 `config/config.yaml`（从 `config/config.example.yaml` 复制），关键配置项：

| 配置项 | 说明 |
|--------|------|
| `platform` | 运行平台 (auto / windows / macos) |
| `ai` | LLM 配置 (provider, api_key, model 等) |
| `auto_reply.rules` | 自动回复规则（关键词/正则/意图） |
| `templates` | 消息模板定义 |
| `workflows` | 工作流状态机定义 |
| `anti_detect` | 防检测参数（发送间隔/频率/熔断） |
| `admin` | 管理后台用户名/密码 |

## 目录结构

```
weix/
├── backend/
│   └── app/
│       ├── core/       # 平台自适应核心（密钥提取/DB读取/消息发送/监听/防检测）
│       ├── ai/         # LangChain AI 引擎（Agent/工具/提示词/记忆/模型）
│       ├── workflow/   # 工作流引擎（规则/模板/状态机/转发）
│       ├── api/        # REST API 路由
│       ├── services/   # 业务逻辑
│       ├── models/     # ORM 模型 + Pydantic schemas
│       └── utils/      # 工具（限流器/日志）
├── frontend/           # Vue3 管理前端
├── config/             # 配置文件
├── scripts/            # 部署脚本
└── README.md
```

## 防封号策略

1. **只读收消息**：从本机数据库读取消息，并验证数据库密钥
2. **GUI 发送**：Windows / macOS 均采用界面操作；发送前核对联系人和聊天标题
3. **频率控制**：全局每分钟 ≤ 20 条，单会话冷却 30s
4. **行为模拟**：发送间隔随机化（Win 15-45s / Mac 8-20s）
5. **熔断保护**：连续失败 3 次暂停 5 分钟

## License

MIT
