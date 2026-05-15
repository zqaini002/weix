#!/usr/bin/env bash
# ============================================================
# Weix - macOS 一键环境初始化脚本
# ============================================================
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; NC="\033[0m"
info()    { printf "${GREEN}[INFO]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC}    %s\n" "$*"; }
error()   { printf "${RED}[ERROR]${NC}   %s\n" "$*"; }
section() { printf "\n${BOLD}%s${NC}\n" "$*"; }

section "Weix - macOS 环境初始化"

# 检查 macOS
if [[ "$(uname -s)" != "Darwin" ]]; then
    error "此脚本仅适用于 macOS 系统"
    exit 1
fi

# 检查 Python
section "检查 Python 环境"
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | awk '{print $2}')
        MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 10 ]]; then
            PYTHON="$cmd"
            info "找到 Python $PY_VER ($PYTHON)"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    error "需要 Python 3.10+，请先安装: brew install python@3.12"
    exit 1
fi

# 创建虚拟环境
if [[ ! -d "venv" ]]; then
    section "创建 Python 虚拟环境"
    "$PYTHON" -m venv venv
    info "虚拟环境已创建: venv/"
fi

source venv/bin/activate
pip install --upgrade pip -q

# 安装依赖
section "安装后端依赖"
pip install -r backend/requirements.txt --ignore-requires-python 2>&1 | tail -5
info "后端依赖安装完成"

# 检查 Node.js
section "检查 Node.js 环境"
if ! command -v node &>/dev/null; then
    warn "未找到 Node.js，跳过前端安装"
    warn "请安装 Node.js 20+: brew install node@20"
else
    NODE_VER=$(node --version)
    info "Node.js $NODE_VER"

    cd frontend
    if [[ ! -d "node_modules" ]]; then
        info "安装前端依赖..."
        npm install --silent 2>&1 | tail -3
    fi
    cd ..
    info "前端依赖安装完成"
fi

# 检查辅助功能权限
section "检查系统权限"
if ! osascript -e 'tell application "System Events" to keystroke "a"' 2>/dev/null; then
    warn "需要授予辅助功能权限"
    warn "请前往: 系统偏好设置 → 隐私与安全性 → 辅助功能"
    warn "添加你的终端应用 (Terminal.app / iTerm.app)"
fi

# 创建配置目录
mkdir -p data

section "初始化完成!"
echo "下一步:"
echo "  1. 编辑 config/config.yaml 配置文件"
echo "  2. 运行 bash scripts/start.sh 启动服务"
echo ""
echo "macOS 注意事项:"
echo "  - 确保已授予终端辅助功能权限"
echo "  - 密钥提取需要 sudo 权限（一次性）"
echo "  - 发送消息时微信窗口需在前台"
