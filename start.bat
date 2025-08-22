@echo off
echo ========================================
echo COPY TRADING BOT - STARTUP SCRIPT
echo ========================================

REM Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo Virtual environment not found!
    echo Please run setup_venv.bat first to create the virtual environment
    echo.
    pause
    exit /b 1
)

REM Check if .env file exists
if not exist ".env" (
    echo WARNING: .env file not found!
    echo Please copy env_example.txt to .env and configure your settings
    echo.
    echo Press any key to continue anyway...
    pause >nul
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Check if main.py exists
if not exist "main.py" (
    echo ERROR: main.py not found!
    echo Please make sure you're in the correct directory
    pause
    exit /b 1
)

echo Starting copy trading bot...
echo.
echo The bot will start two servers:
echo - API Server: http://localhost:8000
echo - Dashboard: http://localhost:5000
echo.
echo Press Ctrl+C to stop the bot
echo.

REM Start the bot
python main.py

pause
