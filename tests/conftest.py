import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quotes import LONDON, Quote, load_day_bars  # noqa: E402


@pytest.fixture
def ispy_bars():
    """Real ISPY.L 1m bars for 2026-09-25 (cached fixture; no network)."""
    return load_day_bars("ISPY.L", "2026-09-25", allow_fetch=False)


@pytest.fixture
def london():
    def _t(h, m, s=0, day=25):
        return datetime(2026, 9, day, h, m, s, tzinfo=LONDON)
    return _t


@pytest.fixture
def make_emulator(tmp_path):
    import emulator

    def _make(now, quotes_by_symbol=None, bars_fn=None):
        qb = quotes_by_symbol or {}

        def quote_fn(sym):
            v = qb.get(sym)
            if isinstance(v, list):
                return v.pop(0) if v else None
            return v
        (tmp_path / "monitor_heartbeat.json").write_text(
            '{"at": "%s", "pid": 0}' % now.isoformat())   # live monitor (SOP v2.2)
        e = emulator.PaperTradingEmulator(data_dir=str(tmp_path), quote_fn=quote_fn,
                                          bars_fn=bars_fn, now_fn=lambda: now)
        e.requote_wait_s = 0
        return e
    return _make
