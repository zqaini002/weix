@echo off
REM ============================================================
REM Weix - Windows 一键启动脚本
REM ============================================================
setlocal

echo ============================================================
echo  Weix - 启动服务 (Windows)
echo ============================================================

REM 切换到项目根目录
cd /d "%~dp0\.."

REM 检查虚拟环境
if not exist "venv" (
    echo [错误] 虚拟环境不存在，请先运行 scripts\setup.bat
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo [启动] FastAPI 后端 (端口 8000)...
start "Weix-Backend" cmd /c "cd /d %CD% && venv\Scripts\python.exe -m app.main"
timeout /t 3 /nobreak >nul

REM 启动前端
if exist "frontend\node_modules" (
    echo [启动] 前端开发服务器 (端口 5173)...
    start "Weix-Frontend" cmd /c "cd /d %CD%\frontend && npm run dev"
)

echo.
echo ============================================================
echo  Weix 服务已启动
echo   后端: http://localhost:8000
echo   后端文档: http://localhost:8000/docs
echo   前端: http://localhost:5173
echo.
echo   关闭窗口停止服务
echo ============================================================

REM 保持窗口打开
pause
