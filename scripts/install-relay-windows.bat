@echo off
REM ============================================================================
REM SCAN Mobile - Gnirehtet Relay Server Installer for Windows
REM ============================================================================
REM
REM This script downloads, installs, and configures the gnirehtet relay server
REM which is required for USB reverse tethering on Honeywell CN80G devices.
REM
REM Prerequisites:
REM   - ADB (Android Debug Bridge) must be installed and in PATH
REM   - Device must be connected via USB with ADB debugging enabled
REM
REM Usage:
REM   install-relay-windows.bat [install|start|stop|status]
REM
REM ============================================================================

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "GNIREHTET_VERSION=2.5.1"
set "GNIREHTET_URL=https://github.com/Genymobile/gnirehtet/releases/download/v%GNIREHTET_VERSION%/gnirehtet-rust-win64-v%GNIREHTET_VERSION%.zip"
set "INSTALL_DIR=%SCRIPT_DIR%gnirehtet"
set "RELAY_PORT=31416"

REM Check for command argument
if "%1"=="" goto :show_help
if "%1"=="install" goto :install
if "%1"=="start" goto :start
if "%1"=="stop" goto :stop
if "%1"=="status" goto :status
if "%1"=="autorun" goto :autorun
if "%1"=="help" goto :show_help
goto :show_help

:show_help
echo.
echo ============================================================================
echo   SCAN Mobile - Gnirehtet Relay Server Manager
echo ============================================================================
echo.
echo   Usage: %~nx0 [command]
echo.
echo   Commands:
echo     install   - Download and install gnirehtet relay server
echo     start     - Start relay server and set up ADB tunnel
echo     stop      - Stop relay server
echo     status    - Show relay server status
echo     autorun   - Start relay and automatically connect devices
echo     help      - Show this help message
echo.
echo   Prerequisites:
echo     - ADB must be installed and in PATH
echo     - Device connected via USB with ADB debugging enabled
echo.
echo   After starting the relay, use the SCAN Mobile app to enable
echo   USB tethering from the device settings.
echo.
echo ============================================================================
goto :eof

:install
echo.
echo ============================================================================
echo   Installing Gnirehtet Relay Server v%GNIREHTET_VERSION%
echo ============================================================================
echo.

REM Check if already installed
if exist "%INSTALL_DIR%\gnirehtet.exe" (
    echo [!] Gnirehtet already installed at: %INSTALL_DIR%
    echo     To reinstall, delete the folder and run install again.
    goto :eof
)

REM Check for curl or PowerShell
where curl >nul 2>&1
if %errorlevel%==0 (
    set "DOWNLOAD_CMD=curl"
) else (
    set "DOWNLOAD_CMD=powershell"
)

echo [1/4] Creating installation directory...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo [2/4] Downloading gnirehtet v%GNIREHTET_VERSION%...
set "ZIP_FILE=%INSTALL_DIR%\gnirehtet.zip"

if "%DOWNLOAD_CMD%"=="curl" (
    curl -L -o "%ZIP_FILE%" "%GNIREHTET_URL%"
) else (
    powershell -Command "Invoke-WebRequest -Uri '%GNIREHTET_URL%' -OutFile '%ZIP_FILE%'"
)

if not exist "%ZIP_FILE%" (
    echo [ERROR] Failed to download gnirehtet
    goto :eof
)

echo [3/4] Extracting files...
powershell -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%INSTALL_DIR%' -Force"

REM Move files from nested folder if needed
if exist "%INSTALL_DIR%\gnirehtet-rust-win64" (
    move "%INSTALL_DIR%\gnirehtet-rust-win64\*" "%INSTALL_DIR%\" >nul 2>&1
    rmdir "%INSTALL_DIR%\gnirehtet-rust-win64" >nul 2>&1
)

echo [4/4] Cleaning up...
del "%ZIP_FILE%" >nul 2>&1

echo.
echo ============================================================================
echo   Installation Complete!
echo ============================================================================
echo.
echo   Gnirehtet installed to: %INSTALL_DIR%
echo.
echo   Next steps:
echo     1. Connect your device via USB
echo     2. Run: %~nx0 start
echo     3. Enable USB tethering in SCAN Mobile app
echo.
goto :eof

:start
echo.
echo ============================================================================
echo   Starting Gnirehtet Relay Server
echo ============================================================================
echo.

REM Check if installed
if not exist "%INSTALL_DIR%\gnirehtet.exe" (
    echo [ERROR] Gnirehtet not installed. Run '%~nx0 install' first.
    goto :eof
)

REM Check for ADB
where adb >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ADB not found in PATH.
    echo         Please install Android SDK Platform Tools and add to PATH.
    goto :eof
)

REM Check for connected device
echo [1/3] Checking for connected devices...
adb devices | findstr /r /c:"device$" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] No device connected or ADB not authorized.
    echo           Please connect device and enable USB debugging.
    echo.
    echo           Continuing anyway - relay will wait for connection...
)

REM Set up ADB reverse tunnel
echo [2/3] Setting up ADB reverse tunnel (port %RELAY_PORT%)...
adb reverse localabstract:gnirehtet tcp:%RELAY_PORT% 2>nul
if %errorlevel%==0 (
    echo         ADB tunnel established successfully
) else (
    echo [WARNING] Could not set up ADB tunnel - device may not be connected
)

REM Start relay server
echo [3/3] Starting relay server on port %RELAY_PORT%...
echo.
echo ============================================================================
echo   Relay server running. Press Ctrl+C to stop.
echo ============================================================================
echo.
echo   Device can now enable USB tethering in SCAN Mobile app.
echo   All device traffic will be routed through this computer.
echo.

cd /d "%INSTALL_DIR%"
gnirehtet.exe relay

goto :eof

:stop
echo.
echo ============================================================================
echo   Stopping Gnirehtet Relay Server
echo ============================================================================
echo.

REM Kill relay process
taskkill /f /im gnirehtet.exe >nul 2>&1
if %errorlevel%==0 (
    echo [OK] Relay server stopped
) else (
    echo [INFO] Relay server was not running
)

REM Remove ADB reverse tunnel
adb reverse --remove localabstract:gnirehtet >nul 2>&1
echo [OK] ADB tunnel removed

goto :eof

:status
echo.
echo ============================================================================
echo   Gnirehtet Relay Server Status
echo ============================================================================
echo.

REM Check installation
if exist "%INSTALL_DIR%\gnirehtet.exe" (
    echo [INSTALLED] Gnirehtet found at: %INSTALL_DIR%
) else (
    echo [NOT INSTALLED] Run '%~nx0 install' to install
    goto :eof
)

REM Check if running
tasklist /fi "imagename eq gnirehtet.exe" 2>nul | find /i "gnirehtet.exe" >nul
if %errorlevel%==0 (
    echo [RUNNING] Relay server is active
) else (
    echo [STOPPED] Relay server is not running
)

REM Check ADB
where adb >nul 2>&1
if %errorlevel%==0 (
    echo [OK] ADB found in PATH

    REM Check for devices
    for /f "tokens=1" %%a in ('adb devices ^| findstr /r /c:"device$"') do (
        echo [CONNECTED] Device: %%a
    )

    REM Check reverse tunnel
    adb reverse --list 2>nul | find "gnirehtet" >nul
    if %errorlevel%==0 (
        echo [OK] ADB reverse tunnel active
    ) else (
        echo [INFO] ADB reverse tunnel not set up
    )
) else (
    echo [WARNING] ADB not found in PATH
)

echo.
goto :eof

:autorun
echo.
echo ============================================================================
echo   Starting Gnirehtet Autorun Mode
echo ============================================================================
echo.
echo   This mode will:
echo     - Start the relay server
echo     - Automatically detect and connect devices
echo     - Reconnect devices if they disconnect
echo.

REM Check if installed
if not exist "%INSTALL_DIR%\gnirehtet.exe" (
    echo [ERROR] Gnirehtet not installed. Run '%~nx0 install' first.
    goto :eof
)

cd /d "%INSTALL_DIR%"
gnirehtet.exe autorun

goto :eof