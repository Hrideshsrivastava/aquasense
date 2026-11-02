import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# every test run gets its own database
os.environ.setdefault("AQUASENSE_DB", str(Path(os.environ.get("TMPDIR", "/tmp")) / f"aquasense_test_{os.getpid()}.db"))
