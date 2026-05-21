#!/usr/bin/env bash
# ============================================================
# Weix - macOS 构建脚本
# 构建前端 + 打包后端为独立应用
# 产物: dist/Weix.app (macOS 应用)
# ============================================================
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; NC="\033[0m"
info()  { printf "${GREEN}[INFO]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}    %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC}   %s\n" "$*"; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="$PROJECT_DIR/build"
FRONTEND_DIST="$PROJECT_DIR/frontend/dist"
BACKEND_DIR="$PROJECT_DIR/backend"

info "=========================================="
info " Weix macOS 构建"
info "=========================================="

# 1. 构建前端
info "构建前端..."
cd "$PROJECT_DIR/frontend"
npm ci --silent
npm run build
cd "$PROJECT_DIR"
info "前端构建完成: $FRONTEND_DIST"

# 2. 安装后端依赖
info "安装后端依赖..."
if [[ -d "venv" ]]; then
    source venv/bin/activate
else
    python3 -m venv venv
    source venv/bin/activate
fi
pip install -q -r "$BACKEND_DIR/requirements.txt"
pip install -q pyinstaller

# 3. PyInstaller 打包
info "打包后端..."

# 收集数据文件
DATA_FLAGS=(
    --add-data "config:config"
    --add-data "data:data"
    --add-data "$FRONTEND_DIST:frontend_dist"
    --add-data "$BACKEND_DIR/app/core/mach_helper:app/core"
)

# 隐藏导入
HIDDEN_IMPORTS=(
    --hidden-import=uvicorn.logging
    --hidden-import=uvicorn.loops.auto
    --hidden-import=uvicorn.protocols.http.auto
    --hidden-import=sqlalchemy.ext.asyncio
    --hidden-import=aiosqlite
    --hidden-import=chromadb
    --hidden-import=sentence_transformers
    --hidden-import=tiktoken
    --hidden-import=langchain
    --hidden-import=jieba
    --hidden-import=passlib.handlers.bcrypt
    --hidden-import=pycryptodome
)

pyinstaller \
    --name=Weix \
    --onedir \
    --console \
    --clean \
    --noconfirm \
    --paths="$BACKEND_DIR" \
    "${DATA_FLAGS[@]}" \
    "${HIDDEN_IMPORTS[@]}" \
    --collect-all chromadb \
    --collect-all sentence_transformers \
    "$BACKEND_DIR/app/main.py"

info "后端打包完成: $DIST_DIR/Weix"

# 4. 创建启动脚本
cat > "$DIST_DIR/Weix/start_weix.sh" << 'LAUNCHER'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
# 启动后端服务
./Weix &
BACKEND_PID=$!
sleep 2
echo "Weix 服务已启动: http://localhost:8000"
echo "前端界面: http://localhost:8000"
echo "按 Ctrl+C 停止"
wait $BACKEND_PID
LAUNCHER
chmod +x "$DIST_DIR/Weix/start_weix.sh"

info "=========================================="
info " 构建完成"
info " 应用目录: $DIST_DIR/Weix"
info " 启动方式: $DIST_DIR/Weix/start_weix.sh"
info "=========================================="
