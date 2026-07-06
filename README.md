# Weix — 微信全 AI 自动回复机器人

接入大模型，让 AI 替你自动回微信。**不封号**。

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
| 微信版本 | PC 微信 3.9.12.51 | Mac 微信 4.x (App Store) |
| DB 路径 | `Documents/WeChat Files/<wxid>/Msg/` | `~/Library/Containers/com.tencent.xinWeChat/...` |
| 密钥提取 | `ReadProcessMemory` (Win32 API) | `mach_vm_read_overwrite` (Mach VM) |
| 消息发送 | pyautogui 模拟鼠标点击 + 右键粘贴 | AppleScript 模拟键盘输入 |
| 发送风险 | 极低（无注入，纯 GUI 模拟） | 极低（与真人操作无异） |
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
git clone <repo-url> weix
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

首次运行会**自动**从本机微信进程内存中提取数据库解密密钥，保存到 `data/all_keys.json`。如果自动提取失败，可手动设置环境变量 `WEIX_WECHAT_DB_KEY`（64 位十六进制密钥）。

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

1. **收消息零风险**：只读数据库文件，微信进程完全无感知
2. **发消息零注入**：Windows / macOS 均采用 GUI 模拟操作，不注入 DLL、不 Hook 进程，与真人操作无异
3. **频率控制**：全局每分钟 ≤ 20 条，单会话冷却 30s
4. **行为模拟**：发送间隔随机化（Win 15-45s / Mac 8-20s）
5. **熔断保护**：连续失败 3 次暂停 5 分钟

## License

MIT
