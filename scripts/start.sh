#!/usr/bin/env bash
# ============================================================
# Weix - macOS 一键启动脚本
# ============================================================
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; NC="\033[0m"
info()  { printf "${GREEN}[INFO]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}    %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC}   %s\n" "$*"; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

info "启动 Weix 服务 (macOS)"

# 检查虚拟环境
if [[ ! -d "venv" ]]; then
    error "虚拟环境不存在，请先运行 bash scripts/setup.sh"
    exit 1
fi

source venv/bin/activate

# 清理
trap 'kill 0; exit' INT TERM

# 启动后端
info "启动 FastAPI 后端 (端口 8000)..."
python -m app.main &
BACKEND_PID=$!
sleep 2

if ! kill -0 $BACKEND_PID 2>/dev/null; then
    error "后端启动失败"
    exit 1
fi

# 启动前端 (开发模式)
if [[ -d "frontend/node_modules" ]]; then
    info "启动前端开发服务器 (端口 5173)..."
    cd frontend
    npm run dev &
    FRONTEND_PID=$!
    cd ..
else
    warn "前端依赖未安装，跳过前端启动"
fi

echo ""
echo "============================================================"
info "Weix 服务已启动"
echo "  后端: http://localhost:8000"
echo "  后端文档: http://localhost:8000/docs"
echo "  前端: http://localhost:5173"
echo ""
info "按 Ctrl+C 停止所有服务"
echo "============================================================"

wait
