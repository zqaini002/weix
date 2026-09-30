@echo off
REM ============================================================
REM Weix - Windows 一键环境初始化脚本
REM ============================================================
setlocal enabledelayedexpansion

echo ============================================================
echo  Weix - Windows 环境初始化
echo ============================================================
echo.

REM 检查管理员权限
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [警告] 建议以管理员权限运行此脚本
    echo        密钥提取需要读取微信进程内存
    echo.
)

REM 检查 Python
echo [检查] Python 环境...
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到 Python，请安装 Python 3.10+
    echo        https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [信息] Python %PYVER%

REM 创建虚拟环境
if not exist "venv" (
    echo.
    echo [创建] Python 虚拟环境...
    python -m venv venv
    echo [完成] 虚拟环境已创建
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q

REM 安装后端依赖
echo.
echo [安装] 后端依赖...
pip install -r backend\requirements.txt
echo [完成] 后端依赖安装完成

REM 预下载 AI 模型
echo.
echo ============================================================
echo  预下载 AI 模型 (~1.4GB)
echo  首次下载约需 5-15 分钟，请耐心等待...
echo ============================================================
if exist "scripts\download_models.py" (
    python scripts\download_models.py
    if %ERRORLEVEL% NEQ 0 (
        echo [警告] 模型预下载未完全成功，首次启动时会自动重试
    )
) else (
    echo [警告] 未找到 scripts\download_models.py，跳过模型预下载
    echo        首次启动时将自动下载模型（可能需要几分钟）
)

REM 引导 .env 配置
echo.
echo ============================================================
echo  环境变量配置
echo ============================================================
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [完成] 已从 .env.example 创建 .env 文件
        echo [重要] 请编辑 .env 填入 DEEPSEEK_API_KEY
    ) else (
        echo [警告] 未找到 .env.example
    )
) else (
    echo [信息] .env 文件已存在，跳过
)

REM 检查 Node.js
echo.
echo [检查] Node.js 环境...
where node >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [警告] 未找到 Node.js，跳过前端安装
    echo       请安装 Node.js 20+: https://nodejs.org/
) else (
    for /f "tokens=*" %%v in ('node --version') do echo [信息] Node.js %%v
    cd frontend
    if not exist "node_modules" (
        echo [安装] 前端依赖...
        call npm install
    )
    cd ..
    echo [完成] 前端依赖安装完成
)

REM 创建目录
if not exist "data" mkdir data

echo.
echo ============================================================
echo  初始化完成!
echo.
echo  下一步:
echo    1. 编辑 .env 填入 DEEPSEEK_API_KEY
echo    2. 编辑 config\config.yaml 调整业务配置
echo    3. 以管理员权限运行 scripts\start.bat
echo.
echo  Windows 注意事项:
echo    - 发送方式: pyautogui 模拟键盘鼠标，无需额外安装
echo    - 密钥提取需管理员权限（读微信进程内存）
echo    - 发送消息时微信窗口需保持前台，不要遮挡
echo    - 关闭微信自动更新，避免 UI 变化导致坐标偏移
echo ============================================================
pause
