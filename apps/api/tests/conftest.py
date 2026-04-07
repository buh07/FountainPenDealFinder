from pathlib import Path
import os
import sys


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


os.environ.setdefault("DATABASE_URL", "sqlite:///./tmp_api_tests.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("USE_FIXTURE_FALLBACK", "true")
