import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db as db_module
import app.services.cashfree as cashfree_module
import app.services.sarvam as sarvam_module


@pytest.fixture(autouse=True)
def force_mocks(monkeypatch):
    """Tests must stay hermetic (fast, free, deterministic) no matter what
    MOCK_* is set to in .env for the real app - never let a real API key make
    a test suite run hit Sarvam/Cashfree over the network."""
    monkeypatch.setattr(sarvam_module, "MOCK_SARVAM", True)
    monkeypatch.setattr(cashfree_module, "MOCK_CASHFREE", True)


@pytest.fixture
def fresh_db(monkeypatch):
    tmp_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path)
    db_module.init_db()
    db_module.seed()
    yield
    Path(tmp_path).unlink(missing_ok=True)
