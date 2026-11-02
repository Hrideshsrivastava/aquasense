@echo off
rem One-command start for Windows.
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  powershell -Command "(Get-Content .env) -replace '^AUTH_DEV=0','AUTH_DEV=1' | Set-Content .env"
  echo Created .env with AUTH_DEV=1 for local demo login. Add Google keys and set AUTH_DEV=0 for real sign-in.
)
python -m simulator.seed
echo Open http://localhost:8000
uvicorn app.main:app --reload
