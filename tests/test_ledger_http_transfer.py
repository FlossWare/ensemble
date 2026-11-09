import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_bidirectional_live_ledger_transfer():
    if not os.getenv("POC_LEDGER_URL") or not os.getenv("ENSEMBLE_LEDGER_URL"):
        pytest.skip("live POC_LEDGER_URL and ENSEMBLE_LEDGER_URL are not configured")
    script = Path(__file__).resolve().parents[1] / "parity" / "ledger_http_transfer.py"
    subprocess.run([sys.executable, str(script)], check=True, timeout=180)
