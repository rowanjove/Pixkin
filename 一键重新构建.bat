@echo off
setlocal

rem Keep the console open even if a later command cannot be parsed or crashes.
if /i "%~1"=="--check" goto :check
if /i not "%~1"=="--run" (
    start "Pixkin Rebuild" cmd.exe /d /k ""%~f0" --run"
    exit /b 0
)

cd /d "%~dp0"
title Pixkin Rebuild

echo.
echo ========================================
echo   Pixkin - Clean Rebuild
echo ========================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python Launcher ^(py.exe^) was not found.
    echo Install Python 3.11 with Python Launcher enabled.
    goto :failed
)

py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11 was not found.
    echo Install Python 3.11 and try again.
    goto :failed
)

echo [1/4] Checking build dependencies...
py -3.11 -c "import PyQt6, openai, requests, yaml, win32cred, PIL, PyInstaller, pytest" >nul 2>&1
if errorlevel 1 (
    echo Installing missing dependencies...
    py -3.11 -m pip install -r requirements-build.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install build dependencies.
        goto :failed
    )
) else (
    echo Dependencies are ready.
)

echo.
echo [2/4] Running automated tests...
set "QT_QPA_PLATFORM=offscreen"
py -3.11 -m pytest -q
if errorlevel 1 (
    echo [ERROR] Tests failed. Build was stopped.
    goto :failed
)

echo.
echo [3/4] Cleaning old files and building release...
set "QT_QPA_PLATFORM="
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_release.ps1"
if errorlevel 1 (
    echo [ERROR] Release build failed.
    goto :failed
)

echo.
echo [4/4] Build completed successfully.
echo Output: %~dp0release
echo.
start "" "%~dp0release"
echo Press any key to close this window.
pause >nul
exit

:check
cd /d "%~dp0"
if not exist "scripts\build_release.ps1" exit /b 1
if not exist "requirements-build.txt" exit /b 1
where py >nul 2>&1
if errorlevel 1 exit /b 1
py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>&1
if errorlevel 1 exit /b 1
echo BAT preflight check passed.
exit /b 0

:failed
echo.
echo The window will stay open so you can read the error above.
echo Press any key to close this window.
pause >nul
exit
