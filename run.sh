#!/usr/bin/env bash
# One-command demo on macOS / Linux:
#   creates .venv, installs packages, trains models if missing, seeds the
#   simulated fleet, starts the cloud server and the Streamlit console.
#   ./run.sh            # then start the rig (real ESP32) or: ./run.sh rig
#   ./run.sh rig        # virtual rig with the fault-injection panel
#   ./run.sh handheld   # technician handheld (windowed)
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
[ -d .venv ] || $PY -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
mkdir -p logs data
[ -f models/fault_classifier.joblib ] || python -m ml.train

case "${1:-}" in
  rig)      exec python -m simulator.virtual_rig --panel ;;
  handheld) exec python -m handheld.app --windowed ;;
esac

python -m simulator.seed_fleet
python -m cloud.server > logs/server.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 3
echo ">> Cloud server on http://localhost:5000 (log: logs/server.log)"
echo ">> In another terminal: ./run.sh rig   (or power the ESP32 rig)"
streamlit run console/app.py
