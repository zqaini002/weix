@echo off
REM ============================================================
REM Weix - Windows 构建脚本
REM 构建前端 + 打包后端为独立应用
REM 产物: dist\Weix\ (目录包含 Weix.exe)
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

REM 3. PyInstaller 打包
echo [3/4] 打包后端...

pyinstaller ^
    --name=Weix ^
    --onedir ^
    --console ^
    --clean ^
    --noconfirm ^
    --paths="%BACKEND_DIR%" ^
    --add-data "config;config" ^
    --add-data "data;data" ^
    --add-data "%FRONTEND_DIST%;frontend_dist" ^
    --hidden-import=uvicorn.logging ^
    --hidden-import=uvicorn.loops.auto ^
    --hidden-import=uvicorn.protocols.http.auto ^
    --hidden-import=sqlalchemy.ext.asyncio ^
    --hidden-import=aiosqlite ^
    --hidden-import=chromadb ^
    --hidden-import=sentence_transformers ^
    --hidden-import=tiktoken ^
    --hidden-import=langchain ^
    --hidden-import=jieba ^
    --hidden-import=passlib.handlers.bcrypt ^
    --hidden-import=pycryptodome ^
    --collect-all chromadb ^
    --collect-all sentence_transformers ^
    "%BACKEND_DIR%\app\main.py"

echo 后端打包完成: %DIST_DIR%\Weix

REM 4. 创建启动脚本
echo [4/4] 创建启动脚本...
(
echo @echo off
echo cd /d "%%~dp0"
echo start "" "Weix.exe"
echo echo Weix 服务已启动: http://localhost:8000
echo echo 前端界面: http://localhost:8000
echo pause
) > "%DIST_DIR%\Weix\start_weix.bat"

echo ==========================================
echo  构建完成
echo  应用目录: %DIST_DIR%\Weix
echo  启动方式: %DIST_DIR%\Weix\start_weix.bat
echo ==========================================
pause
