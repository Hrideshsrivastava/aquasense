@echo off
REM One-command demo on Windows. "run.bat rig" = virtual rig, "run.bat handheld" = handheld.
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt
if not exist logs mkdir logs
if not exist models\fault_classifier.joblib python -m ml.train
if "%1"=="rig" ( python -m simulator.virtual_rig --panel & goto :eof )
if "%1"=="handheld" ( python -m handheld.app --windowed & goto :eof )
python -m simulator.seed_fleet
start "AquaSense cloud" /min cmd /c "python -m cloud.server > logs\server.log 2>&1"
timeout /t 3 > nul
echo Cloud server on http://localhost:5000. In another window: run.bat rig
streamlit run console\app.py
