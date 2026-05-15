@echo off
REM ============================================================
REM Weix - Windows 一键环境初始化脚本
REM ============================================================
setlocal enabledelayedexpansion

echo ============================================================
echo  Weix - Windows 环境初始化
echo ============================================================
echo.

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

REM 检查 WeChatFerry
echo.
echo [检查] WeChatFerry 服务...
echo   请确保 WeChatFerry HTTP 服务已启动 (默认端口 10010)
echo   启动方式: python -m wcfhttp
echo.

REM 检查 Node.js
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
echo    1. 启动 WeChatFerry: python -m wcfhttp
echo    2. 编辑 config\config.yaml 配置文件
echo    3. 以管理员权限运行 scripts\start.bat
echo.
echo  Windows 注意事项:
echo    - 需要管理员权限运行（密钥提取需要读进程内存）
echo    - 微信版本需为 3.9.12.51（关闭自动更新）
echo    - WeChatFerry HTTP 需保持运行
echo ============================================================
pause
