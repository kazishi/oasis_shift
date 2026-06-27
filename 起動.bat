@echo off
cd /d "%~dp0"

echo ========================================
echo  OASIS Shift Tool
echo ========================================

set PYTHON_CMD=

py --version >nul 2>&1
if not errorlevel 1 set PYTHON_CMD=py

if "%PYTHON_CMD%"=="" (
    python --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=python
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] Python not found.
    echo Please install Python 3.12 from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python: %PYTHON_CMD%

if not exist "venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

echo Activating venv...
call venv\Scripts\activate.bat

if not exist "venv\Scripts\pip.exe" (
    echo Installing pip...
    python -m ensurepip --upgrade
)

echo Installing packages...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo Starting Streamlit...
streamlit run app.py

pause