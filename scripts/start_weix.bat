@echo off
title Weix - WeChat Bot
cd /d "%~dp0"

echo ==========================================
echo  Weix 微信自动回复机器人
echo ==========================================

REM 检查可执行文件是否存在
if not exist "Weix.exe" (
    echo 错误: 找不到 Weix.exe
    echo 请确保在正确的目录中运行此脚本
    pause
    exit /b 1
)

REM 检查配置文件是否存在
if not exist "config\config.yaml" (
    echo 错误: 找不到配置文件 config\config.yaml
    echo 请确保 config 目录存在于 Weix.exe 同级目录
    pause
    exit /b 1
)

REM 检查数据目录是否存在
if not exist "data" (
    echo 创建数据目录...
    mkdir data
)

echo 正在启动服务...
echo 启动后请访问: http://localhost:8000
echo.

REM 使用 cmd /k 保持窗口打开，即使程序崩溃也能看到错误信息
cmd /k "Weix.exe & if errorlevel 1 (echo. & echo ========================================== & echo  启动失败！请检查上述错误信息 & echo ==========================================)"
