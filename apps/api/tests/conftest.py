from pathlib import Path
import shutil
import tempfile
import os
import sys
import uuid

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("USE_FIXTURE_FALLBACK", "true")
os.environ.setdefault("YAHOO_AUCTIONS_ENABLED", "false")
os.environ.setdefault("YAHOO_FLEA_MARKET_ENABLED", "false")
os.environ.setdefault("MERCARI_ENABLED", "false")
os.environ.setdefault("RAKUMA_ENABLED", "false")
os.environ.setdefault("DEFAULT_TIMEZONE", "Asia/Tokyo")

TEST_TMP_DIR = Path(tempfile.gettempdir()) / f"fpdf_test_run_{uuid.uuid4().hex}"
TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
TEST_DB_PATH = TEST_TMP_DIR / "api_tests.db"
TEST_TAXONOMY_FEEDBACK_PATH = TEST_TMP_DIR / "taxonomy_feedback_types.jsonl"
TEST_PRICING_FEEDBACK_PATH = TEST_TMP_DIR / "pen_swap_sales_feedback.jsonl"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}")
os.environ.setdefault("TAXONOMY_FEEDBACK_TYPES_PATH", str(TEST_TAXONOMY_FEEDBACK_PATH))
os.environ.setdefault("FEEDBACK_PRICING_LABELS_PATH", str(TEST_PRICING_FEEDBACK_PATH))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ARG001
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)
