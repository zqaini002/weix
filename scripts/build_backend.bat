@echo off
REM ============================================================
REM Weix - Windows 构建脚本
REM 构建前端 + 打包后端为独立应用
REM 产物: dist\Weix\ (目录包含 Weix.exe GUI 启动器)
REM ============================================================
setlocal EnableDelayedExpansion

set "PROJECT_DIR=%~dp0\.."
cd /d "%PROJECT_DIR%"

set "DIST_DIR=%PROJECT_DIR%\dist"
set "BUILD_DIR=%PROJECT_DIR%\build"
set "FRONTEND_DIST=%PROJECT_DIR%\frontend\dist"
set "BACKEND_DIR=%PROJECT_DIR%\backend"

echo ==========================================
echo  Weix Windows 构建
echo ==========================================

REM 0. 检查并创建 .env 文件
if not exist "%PROJECT_DIR%\.env" (
    echo [0/4] 创建默认 .env 文件...
    copy "%PROJECT_DIR%\.env.example" "%PROJECT_DIR%\.env" >nul
    echo 已创建 .env 文件，请根据需要修改配置
)

REM 1. 构建前端
echo [1/4] 构建前端...
cd /d "%PROJECT_DIR%\frontend"
call npm ci --silent
call npm run build
cd /d "%PROJECT_DIR%"
echo 前端构建完成: %FRONTEND_DIST%

REM 2. 安装后端依赖
echo [2/4] 安装后端依赖...
if not exist "venv" (
    python -m venv venv
)
call venv\Scripts\activate.bat
pip install -q -r "%BACKEND_DIR%\requirements.txt"
pip install -q pyinstaller

REM 3. PyInstaller 打包 (以 launcher.py 作为 GUI 入口)
echo [3/4] 打包中 (这可能需要几分钟)...

pyinstaller ^
    --name=Weix ^
    --onedir ^
    --noconsole ^
    --windowed ^
    --clean ^
    --noconfirm ^
    --paths="%BACKEND_DIR%" ^
    --add-data "%PROJECT_DIR%\config;config" ^
    --add-data "%PROJECT_DIR%\data;data" ^
    --add-data "%FRONTEND_DIST%;frontend_dist" ^
    --hidden-import=PyQt6 ^
    --hidden-import=PyQt6.QtWidgets ^
    --hidden-import=PyQt6.QtCore ^
    --hidden-import=PyQt6.QtGui ^
    --hidden-import=uvicorn ^
    --hidden-import=uvicorn.logging ^
    --hidden-import=uvicorn.loops ^
    --hidden-import=uvicorn.loops.auto ^
    --hidden-import=uvicorn.protocols ^
    --hidden-import=uvicorn.protocols.http ^
    --hidden-import=uvicorn.protocols.http.auto ^
    --hidden-import=uvicorn.protocols.websockets ^
    --hidden-import=uvicorn.protocols.websockets.auto ^
    --hidden-import=uvicorn.lifespan ^
    --hidden-import=uvicorn.lifespan.on ^
    --hidden-import=fastapi ^
    --hidden-import=fastapi.staticfiles ^
    --hidden-import=sqlalchemy.ext.asyncio ^
    --hidden-import=aiosqlite ^
    --hidden-import=chromadb ^
    --hidden-import=sentence_transformers ^
    --hidden-import=tiktoken ^
    --hidden-import=langchain ^
    --hidden-import=langchain_community ^
    --hidden-import=langchain_core ^
    --hidden-import=langgraph ^
    --hidden-import=jieba ^
    --hidden-import=passlib.handlers.bcrypt ^
    --hidden-import=pycryptodome ^
    --hidden-import=yaml ^
    --hidden-import=pydantic ^
    --hidden-import=pydantic_settings ^
    --hidden-import=httpx ^
    --hidden-import=apscheduler ^
    --collect-all chromadb ^
    --collect-all sentence_transformers ^
    "%BACKEND_DIR%\launcher.py"

if errorlevel 1 (
    echo 打包失败！请检查错误信息。
    pause
    exit /b 1
)

echo 后端打包完成: %DIST_DIR%\Weix

REM 4. 清理构建临时文件
echo [4/4] 清理临时文件...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%PROJECT_DIR%\Weix.spec" del "%PROJECT_DIR%\Weix.spec"

echo ==========================================
echo  构建完成!
echo  应用目录: %DIST_DIR%\Weix
echo  启动方式: 双击 %DIST_DIR%\Weix\Weix.exe
echo ==========================================
pause
