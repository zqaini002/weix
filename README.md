# Weix - 微信机器人助手

基于数据库读取的微信机器人，**不被封号**。

## 核心策略

- **收消息**：直接读取微信本地 SQLite 数据库（完全不可检测，纯文件 I/O）
- **发消息**：
  - Windows：WeChatFerry HTTP API（DLL 注入，中等风险）
  - macOS：AppleScript 模拟键盘输入（极低风险，与真人操作无异）
- **AI**：LangChain 编排云端 LLM（DashScope / OpenAI / 硅基流动）
- **管理**：Vue3 + Element Plus Web 前端可视化配置

## 功能

- 自动回复（关键词 / 正则 / 意图三级匹配 + 优先级排序）
- 工作流引擎（陪玩点单流程：填单 → 确认 → 转发接单群 → 分配）
- 消息模板（文本 / 卡片 / 表单 / 列表，支持变量替换）
- 转发规则（关键词 / 工作流事件触发，多目标群转发）
- AI 智能对话（LangChain Agent + 工具调用 + 多轮记忆）
- 统计分析（发言排行 / 时段热力图 / TF-IDF 关键词 / AI 摘要）
- 定时任务（日报 / 周报 / 数据清理 / 健康检查）
- 防封号策略（频率控制 / 行为模拟 / 熔断保护）

## 系统架构

```
微信客户端 ──(只读)──▶ 数据库解密层 ──▶ 消息监听器 ──▶ 消息路由
                                                     │
                                          ┌──────────┼──────────┐
                                          ▼          ▼          ▼
                                      规则引擎   工作流引擎   AI Agent
                                          │          │          │
                                          └──────────┼──────────┘
                                                     ▼
                                              消息发送层
                                          ┌─────────┴─────────┐
                                          ▼                   ▼
                                   Windows (WCF)      macOS (AppleScript)
```

## 平台支持

| 维度 | Windows | macOS |
|------|---------|-------|
| 微信版本 | PC 微信 3.9.12.51 | Mac 微信 4.x (App Store) |
| DB 路径 | `Documents/WeChat Files/<wxid>/Msg/` | `~/Library/Containers/com.tencent.xinWeChat/...` |
| 密钥提取 | `ReadProcessMemory` (Win32 API) | `mach_vm_read_overwrite` (Mach VM) |
| 消息发送 | WeChatFerry HTTP (:10010) | AppleScript 模拟键盘输入 |
| 发送风险 | 中等 | 极低 |
| 管理员权限 | 需要 | 需要 |

## 技术栈

- 后端：FastAPI + SQLAlchemy (async) + aiosqlite
- AI：LangChain 0.3+ + LangGraph
- 前端：Vue 3 + Element Plus + ECharts + Pinia
- 定时：APScheduler
- 数据库解密：pycryptodome (SQLCipher 4)

## 快速开始

### macOS

```bash
# 1. 环境初始化
bash scripts/setup.sh

# 2. 编辑配置
vim config/config.yaml

# 3. 授予辅助功能权限
# 系统偏好设置 → 隐私与安全性 → 辅助功能 → 添加终端

# 4. 启动（首次提取密钥需 sudo）
sudo bash scripts/start.sh
```

### Windows

```cmd
REM 1. 启动 WeChatFerry HTTP 服务（管理员权限）
python -m wcfhttp

REM 2. 环境初始化
scripts\setup.bat

REM 3. 编辑配置
notepad config\config.yaml

REM 4. 以管理员权限启动
scripts\start.bat
```

首次运行会提取数据库解密密钥并保存到 `data/all_keys.json`。

## 管理后台

访问 http://localhost:5173，默认登录密码在 `config/config.yaml` 中配置。

- **仪表盘**：在线状态、消息数、活跃群聊、订单数
- **统计报告**：发言排行、时段分布、关键词、AI 摘要
- **消息日志**：历史消息查询与详情
- **聊天配置**：群聊白名单、私聊权限、回复模式
- **自动回复规则**：关键词/正则/意图规则管理
- **消息模板**：文本/卡片/表单/列表模板编辑器
- **工作流配置**：状态机定义（默认含陪玩点单流程）
- **转发规则**：触发条件 + 目标群配置
- **AI 配置**：Provider、API Key、模型、System Prompt
- **定时任务**：日报/周报/健康检查/数据清理管理

## 配置文件

主配置 `config/config.yaml`，关键配置项：

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
2. **频率控制**：全局每分钟 ≤ 20 条，单会话冷却 30s
3. **行为模拟**：发送间隔随机化（Win 15-45s / Mac 8-20s）
4. **熔断保护**：连续失败 3 次暂停 5 分钟
5. **macOS 天然优势**：AppleScript 键盘模拟 = 真人操作，无法区分

## License

MIT
