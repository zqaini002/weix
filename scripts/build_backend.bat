@echo off
setlocal

REM ============================================================
REM Weix - Windows build script
REM Builds frontend and packages backend launcher with PyInstaller.
REM Output: dist\Weix\Weix.exe
REM ============================================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "DIST_DIR=%PROJECT_DIR%\dist"
set "BUILD_DIR=%PROJECT_DIR%\build"
set "FRONTEND_DIST=%PROJECT_DIR%\frontend\dist"
set "BACKEND_DIR=%PROJECT_DIR%\backend"

cd /d "%PROJECT_DIR%" || exit /b 1

echo ==========================================
echo  Weix Windows Build
echo ==========================================

REM 0. Create default .env if needed.
if not exist "%PROJECT_DIR%\.env" (
    echo [0/4] Creating default .env...
    if exist "%PROJECT_DIR%\.env.example" (
        copy "%PROJECT_DIR%\.env.example" "%PROJECT_DIR%\.env" >nul
    )
)

REM 1. Build frontend.
echo [1/4] Building frontend...
cd /d "%PROJECT_DIR%\frontend" || exit /b 1
if exist "%PROJECT_DIR%\frontend\node_modules" (
    echo Removing stale frontend node_modules...
    rmdir /s /q "%PROJECT_DIR%\frontend\node_modules" >nul 2>nul
)
call npm ci --silent
if errorlevel 1 (
    echo npm ci failed.
    pause
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo Frontend build failed.
    pause
    exit /b 1
)
cd /d "%PROJECT_DIR%" || exit /b 1
echo Frontend build done: %FRONTEND_DIST%

REM 2. Install backend dependencies.
echo [2/4] Installing backend dependencies...
if not exist "%PROJECT_DIR%\venv\Scripts\python.exe" (
    python -m venv "%PROJECT_DIR%\venv"
    if errorlevel 1 (
        echo Failed to create Python virtual environment.
        pause
        exit /b 1
    )
)
call "%PROJECT_DIR%\venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate Python virtual environment.
    pause
    exit /b 1
)
python -m pip install -q -r "%BACKEND_DIR%\requirements.txt"
if errorlevel 1 (
    echo Backend dependency installation failed.
    pause
    exit /b 1
)
python -m pip install -q pyinstaller
if errorlevel 1 (
    echo PyInstaller installation failed.
    pause
    exit /b 1
)

REM 3. Package with PyInstaller.
echo [3/4] Packaging backend...

REM Close old Weix.exe and remove stale output directory.
echo Closing old Weix.exe if running...
taskkill /F /T /IM Weix.exe >nul 2>nul
ping 127.0.0.1 -n 3 >nul

if exist "%DIST_DIR%\Weix" (
    echo Removing old output: %DIST_DIR%\Weix
    rmdir /s /q "%DIST_DIR%\Weix" >nul 2>nul
)

if exist "%DIST_DIR%\Weix" (
    echo Normal delete failed, trying PowerShell cleanup...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -LiteralPath '%DIST_DIR%\Weix' -Recurse -Force"
    if errorlevel 1 (
        echo Failed to remove old dist\Weix. Close Weix.exe, close Explorer windows opened in dist\Weix, or reboot and try again.
        pause
        exit /b 1
    )
)

python -m PyInstaller ^
    --name=Weix ^
    --onedir ^
    --noconsole ^
    --windowed ^
    --clean ^
    --noconfirm ^
    --paths="%BACKEND_DIR%" ^
    --add-data "%PROJECT_DIR%\config;config" ^
    --add-data "%PROJECT_DIR%\data;data" ^
    --add-data "%PROJECT_DIR%\tools;tools" ^
    --add-data "%FRONTEND_DIST%;frontend_dist" ^
    --hidden-import=app.core.wechat_paths_windows ^
    --hidden-import=app.core.windows_dbkey_hook ^
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
    --hidden-import=langchain_openai ^
    --hidden-import=langchain_chroma ^
    --hidden-import=langgraph ^
    --hidden-import=jieba ^
    --hidden-import=passlib.handlers.bcrypt ^
    --hidden-import=jose ^
    --hidden-import=jwt ^
    --hidden-import=Crypto ^
    --hidden-import=pyautogui ^
    --hidden-import=pyperclip ^
    --hidden-import=pygetwindow ^
    --hidden-import=psutil ^
    --hidden-import=yaml ^
    --hidden-import=pydantic ^
    --hidden-import=pydantic_core ^
    --hidden-import=pydantic_core._pydantic_core ^
    --hidden-import=pydantic_settings ^
    --hidden-import=httpx ^
    --hidden-import=apscheduler ^
    --hidden-import=wordcloud ^
    --hidden-import=matplotlib ^
    --hidden-import=PIL ^
    --hidden-import=tiktoken_ext.openai_public ^
    --collect-all chromadb ^
    --collect-all langchain_community ^
    --collect-all langchain_openai ^
    --collect-all langchain_chroma ^
    --collect-all pydantic ^
    --collect-all pydantic_core ^
    --collect-all pydantic_settings ^
    --collect-all tiktoken ^
    --collect-all sentence_transformers ^
    --collect-all tokenizers ^
    --collect-all transformers ^
    "%BACKEND_DIR%\launcher.py"

if errorlevel 1 (
    echo PyInstaller packaging failed.
    pause
    exit /b 1
)

REM 4. Cleanup build files.
echo [4/4] Cleaning temporary files...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%PROJECT_DIR%\Weix.spec" del "%PROJECT_DIR%\Weix.spec"

echo ==========================================
echo  Build complete.
echo  App dir: %DIST_DIR%\Weix
echo  Run: %DIST_DIR%\Weix\Weix.exe
echo ==========================================
pause
