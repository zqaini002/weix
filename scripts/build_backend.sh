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

# 0. 检查并创建 .env 文件
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    info "创建默认 .env 文件..."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "已创建 .env 文件，请根据需要修改配置"
fi

# 1. 构建前端
info "[1/4] 构建前端..."
cd "$PROJECT_DIR/frontend"
npm ci --silent
npm run build
cd "$PROJECT_DIR"
info "前端构建完成: $FRONTEND_DIST"

# 2. 安装后端依赖
info "[2/4] 安装后端依赖..."
if [[ -d "venv" ]]; then
    source venv/bin/activate
else
    python3 -m venv venv
    source venv/bin/activate
fi
pip install -q -r "$BACKEND_DIR/requirements.txt"
pip install -q pyinstaller

# 3. PyInstaller 打包 (以 launcher.py 作为 GUI 入口)
info "[3/4] 打包中 (这可能需要几分钟)..."

# 收集数据文件
DATA_FLAGS=(
    --add-data "config:config"
    --add-data "data:data"
    --add-data "$FRONTEND_DIST:frontend_dist"
    --add-data "$BACKEND_DIR/app/core/mach_helper:app/core"
)

# 隐藏导入
HIDDEN_IMPORTS=(
    --hidden-import=PyQt6
    --hidden-import=PyQt6.QtWidgets
    --hidden-import=PyQt6.QtCore
    --hidden-import=PyQt6.QtGui
    --hidden-import=uvicorn
    --hidden-import=uvicorn.logging
    --hidden-import=uvicorn.loops
    --hidden-import=uvicorn.loops.auto
    --hidden-import=uvicorn.protocols
    --hidden-import=uvicorn.protocols.http
    --hidden-import=uvicorn.protocols.http.auto
    --hidden-import=uvicorn.protocols.websockets
    --hidden-import=uvicorn.protocols.websockets.auto
    --hidden-import=uvicorn.lifespan
    --hidden-import=uvicorn.lifespan.on
    --hidden-import=fastapi
    --hidden-import=fastapi.staticfiles
    --hidden-import=sqlalchemy.ext.asyncio
    --hidden-import=aiosqlite
    --hidden-import=chromadb
    --hidden-import=sentence_transformers
    --hidden-import=tiktoken
    --hidden-import=langchain
    --hidden-import=langchain_community
    --hidden-import=langchain_core
    --hidden-import=langgraph
    --hidden-import=jieba
    --hidden-import=passlib.handlers.bcrypt
    --hidden-import=pycryptodome
    --hidden-import=yaml
    --hidden-import=pydantic
    --hidden-import=pydantic_settings
    --hidden-import=httpx
    --hidden-import=apscheduler
)

pyinstaller \
    --name=Weix \
    --onedir \
    --windowed \
    --clean \
    --noconfirm \
    --paths="$BACKEND_DIR" \
    "${DATA_FLAGS[@]}" \
    "${HIDDEN_IMPORTS[@]}" \
    --collect-all chromadb \
    --collect-all sentence_transformers \
    "$BACKEND_DIR/launcher.py"

if [[ $? -ne 0 ]]; then
    error "打包失败！请检查错误信息。"
    exit 1
fi

info "后端打包完成: $DIST_DIR/Weix"

# 4. 清理构建临时文件
info "[4/4] 清理临时文件..."
rm -rf "$BUILD_DIR"
rm -f "$PROJECT_DIR/Weix.spec"

# 5. 组装 macOS .app 应用包
info "组装 macOS .app 应用包..."
APP_BUNDLE="$DIST_DIR/Weix.app"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

mv "$DIST_DIR/Weix" "$APP_BUNDLE/Contents/Resources/"

cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Weix</string>
    <key>CFBundleIdentifier</key>
    <string>com.weix.app</string>
    <key>CFBundleName</key>
    <string>Weix</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleIconFile</key>
    <string>Weix.icns</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# 创建启动脚本 -> 直接调用 Weix (PyQt6 GUI 入口)
cat > "$APP_BUNDLE/Contents/MacOS/Weix" << 'LAUNCHER'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources/Weix" && pwd)"
cd "$DIR"
exec "$DIR/Weix" "$@"
LAUNCHER
chmod +x "$APP_BUNDLE/Contents/MacOS/Weix"

info "=========================================="
info " 构建完成!"
info " 应用目录: $APP_BUNDLE"
info " 启动方式: 双击打开 Weix.app"
info "=========================================="
