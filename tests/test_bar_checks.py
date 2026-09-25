from datetime import datetime

import pandas as pd
import pytest

from bar_checks import check_position, evaluate_bars
from quotes import LONDON


def bars(rows, start="2026-09-25 10:00"):
    idx = pd.date_range(start, periods=len(rows), freq="1min", tz=LONDON)
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def test_stop_at_level_when_not_gapped():
    b = bars([(100, 101, 99.5, 100), (100, 100.5, 98.0, 98.5)])
    ex, *_ = evaluate_bars(b, stop=99.0, target=105, bad_tick_pct=0)
    assert ex.kind == "stop" and ex.price == 99.0 and not ex.gapped
    assert ex.bar_ts.minute == 1


def test_stop_gap_fills_at_open():
    b = bars([(100, 100, 99.5, 99.6), (97.0, 97.5, 96.0, 97.2)])
    ex, *_ = evaluate_bars(b, stop=99.0, target=105, bad_tick_pct=0)
    assert ex.kind == "stop" and ex.price == 97.0 and ex.gapped


def test_target_exits_at_target():
    b = bars([(100, 106, 99.8, 105)])
    ex, *_ = evaluate_bars(b, stop=99.0, target=105, bad_tick_pct=0)
    assert ex.kind == "target" and ex.price == 105


def test_both_in_one_bar_stop_wins():
    b = bars([(100, 106, 98, 104)])
    ex, *_ = evaluate_bars(b, stop=99.0, target=105, bad_tick_pct=0)
    assert ex.kind == "stop" and ex.price == 99.0


def test_last_check_skips_old_bars_and_completed_only():
    b = bars([(100, 100, 98, 99), (100, 101, 99.5, 100), (100, 101, 99.5, 100)])
    t = lambda m: datetime(2026, 9, 25, 10, m, tzinfo=LONDON)
    # opened before first bar, but last_check already past the stop bar
    r = check_position(b, stop=99.0, target=105, opened_at=t(0), last_check=t(1), now=t(3))
    assert r.exit is None and r.last_check == t(3) and r.bars_scanned == 2
    # completed-only: at 10:00:30 the 10:00 bar is still forming -> not used
    r = check_position(b, stop=99.0, target=105, opened_at=t(0), now=datetime(2026, 9, 25, 10, 0, 30, tzinfo=LONDON))
    assert r.exit is None and r.bars_scanned == 0
    r = check_position(b, stop=99.0, target=105, opened_at=t(0), now=t(1), bad_tick_pct=0)
    assert r.exit and r.exit.kind == "stop"


def test_unit_conversion_gbx():
    b = bars([(3750, 3750, 3728, 3730)])
    ex, *_ = evaluate_bars(b, stop=37.3725, target=38.69, to_book=lambda x: x / 100)
    assert ex.kind == "stop" and ex.price == pytest.approx(37.3725)


def test_bad_tick_skipped_when_next_bar_does_not_confirm():
    # 14:21 3738 -> 14:28 3665 (30 sh) -> 14:33 3717.62 -> 14:34 3695 (ISPY 25 Sep shape)
    idx = pd.to_datetime(["2026-09-25 14:21", "2026-09-25 14:28", "2026-09-25 14:33",
                          "2026-09-25 14:34"]).tz_localize(LONDON)
    b = pd.DataFrame([(3738, 3738, 3738, 3738), (3665, 3665, 3665, 3665),
                      (3717.62, 3717.62, 3717.62, 3717.62), (3695, 3695, 3695, 3695)],
                     columns=["Open", "High", "Low", "Close"], index=idx)
    ex, n, hold, skipped = evaluate_bars(b, stop=37.1597, target=38.47, to_book=lambda x: x / 100)
    assert [t.strftime("%H:%M") for t in skipped] == ["14:28"]
    assert ex.kind == "stop" and ex.bar_ts.strftime("%H:%M") == "14:34"
    assert ex.gapped and ex.price == pytest.approx(36.95)


def test_bad_tick_holds_until_next_bar_live():
    idx = pd.to_datetime(["2026-09-25 14:21", "2026-09-25 14:28"]).tz_localize(LONDON)
    b = pd.DataFrame([(3738, 3738, 3738, 3738), (3665, 3665, 3665, 3665)],
                     columns=["Open", "High", "Low", "Close"], index=idx)
    t = lambda h, m, s=0: datetime(2026, 9, 25, h, m, s, tzinfo=LONDON)
    r = check_position(b, stop=37.1597, target=38.47, opened_at=t(8, 22, 21), now=t(14, 29, 5),
                       to_book=lambda x: x / 100)
    assert r.exit is None and r.last_check == t(14, 28)       # re-examine 14:28 next pass
    assert r.last_close == pytest.approx(37.38)                # not marked to the bad tick


def test_bad_tick_confirmed_by_next_bar_exits():
    idx = pd.to_datetime(["2026-09-25 14:21", "2026-09-25 14:28", "2026-09-25 14:29"]).tz_localize(LONDON)
    b = pd.DataFrame([(3738, 3738, 3738, 3738), (3665, 3665, 3665, 3665), (3670, 3680, 3660, 3670)],
                     columns=["Open", "High", "Low", "Close"], index=idx)
    ex, *_ = evaluate_bars(b, stop=37.1597, target=38.47, to_book=lambda x: x / 100)
    assert ex.bar_ts.strftime("%H:%M") == "14:28" and ex.price == pytest.approx(36.65)
