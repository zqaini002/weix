Always respond in Chinese-simplified.

# Superpowers 强制要求

处理任何任务（开发、调试、重构、搜索代码等）前，必须先检查并调用匹配的 Superpowers Skill 或 Agent。即使只有 1% 的相关性，也必须先调用 Skill 工具。

## 触发规则
- 涉及后端 Python 代码 → 调用 python-development agent
- 涉及前端 Vue 代码 → 调用 full-stack-orchestration agent
- 探索/搜索代码库 → 调用 Explore agent
- 代码审查 → 调用相关 review agent
- 调试问题 → 先调用相关 skill 再分析
- 任何复杂多步骤任务 → 先调用 planner agent 拆解

## 优先级
1. 用户明确指令（CLAUDE.md 等）— 最高优先级
2. Superpowers Skills/Agents — 必须在行动前调用
3. 默认系统行为 — 最低优先级

# 项目信息
- 项目名称: weix (微信自动回复机器人)
- 后端: Python FastAPI, 路径 backend/
- 前端: Vue 3 + Element Plus, 路径 frontend/
- 虚拟环境: venv/
