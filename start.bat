@echo off
REM Bambu Farm — double-click this file to start the app
cd /d "%~dp0"

REM Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install Python dependencies if needed
pip install -r requirements.txt --quiet

REM Open the app
streamlit run app.py
