#!/usr/bin/env bash
# One-command start for macOS/Linux. Creates a venv, installs, seeds demo data, starts the server.
set -e
set -x
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i.bak 's/^AUTH_DEV=0/AUTH_DEV=1/' .env && rm -f .env.bak
  echo ">> Created .env with AUTH_DEV=1 (local demo login). Add Google keys and set AUTH_DEV=0 for real sign-in."
fi
python -m simulator.seed
echo ">> Open http://localhost:8000"
uvicorn app.main:app --reload
