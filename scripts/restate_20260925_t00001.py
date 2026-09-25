#!/usr/bin/env python3
"""One-off restatement of T00001/T00002 (ISPY.L, 2026-09-25).

Finding: T00001 was booked at 37.75 (3775p) at 08:22:21, but 3775p printed
only once all day, in the 08:04 1m bar (5 shares). The 08:22 bar is a single
print at 3753.513916p (O=H=L=C, vol 768). The fill used an ~18-minute-old
quote, far beyond the 60s freshness limit.

Restatement (paper book only):
  * entry = 08:22 bar Open (== Close; single-print bar). Open is used because
    it is the first print of the fill minute and so cannot look ahead of the
    08:22:21 fill; here Open == Close so the choice does not change numbers.
  * qty = 12000 / entry; stop = entry * 0.99; target = entry * 1.025
    (same +2.5% the original 38.6937 target used: 37.75 * 1.025).
  * exit re-derived with bar rules on the NEW stop: first 1m bar with
    Low <= stop, exit at min(stop, bar Open).
Requires data/ backup in data_backup_20260925/. Idempotent: refuses to run
unless T00001 still shows the original 37.75 fill.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quotes  # noqa: E402
from bar_checks import check_position  # noqa: E402
from instruments import resolve  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BACKUP = os.path.join(ROOT, "data_backup_20260925")
NOTE = "restated: original fill used stale 08:04 print"
SIZE = 12000.0


def rd(p):
    with open(os.path.join(DATA, p)) as f:
        return json.load(f)


def wr(p, obj):
    path = os.path.join(DATA, p)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def main():
    assert os.path.isdir(BACKUP), "back up data/ to data_backup_20260925/ first"
    trades = rd("trades.json")
    t1 = next(t for t in trades if t["id"] == "T00001")
    t2 = next(t for t in trades if t["id"] == "T00002")
    if abs(t1["price"] - 37.75) > 1e-9:
        print("T00001 already restated; nothing to do")
        return 0

    inst = resolve("ISPY.L")
    bars = quotes.load_day_bars("ISPY.L", "2026-09-25", allow_fetch=False)
    import pandas as pd
    b0804 = bars[bars.index == pd.Timestamp("2026-09-25 08:04", tz=quotes.LONDON)]
    b0822 = bars[bars.index == pd.Timestamp("2026-09-25 08:22", tz=quotes.LONDON)].iloc[0]
    prints_3775 = bars[(bars.Low <= 3775) & (bars.High >= 3775)]
    assert list(prints_3775.index.strftime("%H:%M")) == ["08:04"], prints_3775

    entry = round(inst.to_major(float(b0822.Open)), 4)          # 37.5351
    qty = SIZE / entry
    stop = round(entry * 0.99, 4)
    target = round(entry * 1.025, 4)
    res = check_position(bars, stop=stop, target=target, opened_at="2026-09-25T08:22:21",
                         now=datetime.fromisoformat("2026-09-25T16:35:00+01:00"),
                         to_book=inst.to_major)
    assert res.exit is not None and res.exit.kind == "stop", res
    ex = res.exit
    exit_px = round(ex.price, 4)
    proceeds = qty * exit_px
    pnl = proceeds - SIZE
    exit_ts = ex.bar_ts.isoformat()
    now = quotes.now_london().isoformat()
    cash0 = 20000.0 - SIZE + proceeds   # book started the day flat with £20,000

    orig = {k: t1[k] for k in ("price", "quantity", "stop_loss", "take_profit", "close_price",
                                "close_timestamp", "pnl")}
    orig2 = {k: t2[k] for k in ("price", "quantity", "value_gbp", "timestamp", "pnl")}

    t1.update({
        "price": entry, "quantity": qty, "value_gbp": SIZE,
        "stop_loss": stop, "take_profit": target,
        "status": "stopped_out", "close_price": exit_px, "close_timestamp": exit_ts, "pnl": pnl,
        "quote_ts": "2026-09-25T08:22:00+01:00",
        "quote_source": "yfinance_1m_bar_open(restated)",
        "isin": inst.isin, "ticket_symbol": "ISPY",
        "reasoning": t1["reasoning"].replace(" Stop exit 09:10 BST at 3728p (T00002).", "")
        + f" || {NOTE}. Original booking: 37.75 (3775p) = the 08:04 bar's only 3775p print "
          f"(5 shares), ~18m21s old at the 08:22:21 fill (limit 60s). 08:22 bar was a single print "
          f"O=H=L=C={b0822.Open:.6f}p vol {int(b0822.Volume)}. Restated at the 08:22 bar Open "
          f"(=Close) {entry} GBP; qty {qty:.4f}; stop 1% below {stop}; target +2.5% {target}. "
          f"Original values: {json.dumps(orig)}. NB ticket intended CIBR (IE00BF16M727) not ISPY "
          f"(IE00BYPLS672) - alias error; kept as ISPY since that is what was booked.",
    })
    t2.update({
        "price": exit_px, "quantity": qty, "value_gbp": proceeds,
        "timestamp": exit_ts, "close_price": exit_px, "close_timestamp": exit_ts, "pnl": pnl,
        "status": "stopped_out",
        "quote_ts": exit_ts, "quote_source": "yfinance_1m_bar",
        "isin": inst.isin, "ticket_symbol": "ISPY",
        "reasoning": (
            f"SELL — stopped_out ({NOTE}). With the restated stop {stop} ({stop*100:.2f}p) the "
            f"09:10 bar (low 3728p) no longer breaches. "
            + "".join(
                f"The {bt:%H:%M} print ({float(bars[bars.index == bt].iloc[0].Low):.2f}p, "
                f"{int(bars[bars.index == bt].iloc[0].Volume)} sh) was skipped as a bad tick: >1.5% from the "
                f"prior close and not confirmed by the next bar (next print back above the stop; USPY.L/CIBR.L "
                f"flat at that minute). " for bt in res.skipped_bad_ticks)
            + f"First valid 1m bar with Low <= stop: {ex.bar_ts:%H:%M} BST, O={ex.bar_open*100:.2f}p "
            f"H={ex.bar_high*100:.2f}p L={ex.bar_low*100:.2f}p C={ex.bar_close*100:.2f}p; "
            + (f"it opened below the stop (no print between the stop and the Open), so exit = bar Open "
               f"{exit_px} (min(stop, Open)). " if ex.gapped else f"exit at the stop {exit_px}. ")
            + f"P&L {pnl:+.2f}. Original T00002: {json.dumps(orig2)}."
        ),
    })
    wr("trades.json", trades)

    port = rd("portfolio.json")
    port["cash"] = cash0
    wr("portfolio.json", port)

    day_pnl = round(pnl, 2)
    halted = day_pnl <= -200.0
    ls = rd("live_status.json")
    ls["cash"] = round(cash0, 2)
    ls["day_pnl"] = day_pnl
    ls["remaining_day_risk_gbp"] = round(max(0.0, day_pnl + 200.0), 2)
    ls["halted"] = halted
    if halted:
        ls["halted_date"] = "2026-09-25"
        ls["halt_reason"] = (f"Restated day P&L {day_pnl:+.2f} <= -200 day stop (ISPY restated exit "
                             f"{exit_ts[11:16]} BST). Book already flat; no new fills on 2026-09-25. "
                             f"Resets at next London session (Mon 28 Sep).")
    ls["status_date"] = "2026-09-25"
    ls["closed_today"] = [{
        "trade_id": "T00002", "ticker": "ISPY.L", "freetrade_ticker": "ISPY", "side": "SELL",
        "status": "stopped_out", "entry_gbp": entry, "exit_gbp": exit_px,
        "exit_gbx": round(exit_px * 100, 2), "invest_gbp": SIZE, "qty": qty,
        "pnl_gbp": day_pnl, "stop_gbp": stop, "target_gbp": target, "freetrade": True,
        "exit_at": exit_ts, "detected_at": now, "quote_ts": exit_ts, "quote_source": "yfinance_1m_bar",
        "restated": True, "note": NOTE,
        "original": {"entry_gbp": 37.75, "exit_gbp": 37.28, "exit_at": "2026-09-25T09:10:00+01:00",
                     "qty": orig["quantity"], "pnl_gbp": -149.4, "stop_gbp": 37.3725},
    }]
    sk = ls.get("skipped", {})
    if "ISPY_reentry" in sk:
        sk["ISPY_reentry"]["reason"] = sk["ISPY_reentry"]["reason"].replace(
            "prior filled entry 3775p", "restated entry 3753.51p (orig 3775p was a stale 08:04 print)")
    if halted:
        ls["next_action"] = (f"Flat. Day halted: restated day P&L {day_pnl:+.2f} breached the -£200 day stop. "
                             "No new fills today. Day risk resets Monday 28 Sep 08:00.")
    else:
        ls["next_action"] = (f"Flat. Restated day P&L {day_pnl:+.2f}; remaining day risk "
                             f"£{ls['remaining_day_risk_gbp']:.2f}. No new fills today (window closed). "
                             "Day risk resets Monday 28 Sep 08:00.")
        ls.pop("halt_reason", None)
        ls.pop("halted_date", None)
    if "CIBR.L" in sk:
        sk["CIBR.L"]["registry_note"] = (
            "CIBR.L is the genuine LSE USD line of First Trust Nasdaq Cybersecurity UCITS (IE00BF16M727; "
            "GBX line is FCBR). A USD quote is expected, not an alias problem. ISPY.L (IE00BYPLS672) is a "
            "different fund; instruments.py now refuses to fill a CIBR ticket as ISPY.")
    ls["restatement"] = {"at": now, "note": NOTE, "trades": ["T00001", "T00002"],
                         "backup": "data_backup_20260925/"}
    wr("live_status.json", ls)

    snap = rd("dashboard_snapshot.json")
    snap.update({"updated_at": now, "cash": round(cash0, 2), "portfolio_value": round(cash0, 2),
                 "unrealised_pnl": 0.0, "realised_pnl": day_pnl, "day_pnl": day_pnl,
                 "positions": [], "restated": NOTE})
    wr("dashboard_snapshot.json", snap)

    cg = rd("cond_go_state.json")
    cg["book"].update({"cash": cash0, "day_pnl": day_pnl,
                       "remaining_day_risk_gbp": ls["remaining_day_risk_gbp"], "halted": halted,
                       "restated": NOTE})
    wr("cond_go_state.json", cg)

    rs_path = os.path.join(DATA, "restatements.json")
    rs = json.load(open(rs_path)) if os.path.exists(rs_path) else []
    rs.append({"at": now, "note": NOTE, "T00001": {"before": orig, "after": {
        "price": entry, "quantity": qty, "stop_loss": stop, "take_profit": target}},
        "T00002": {"before": orig2, "after": {"price": exit_px, "timestamp": exit_ts, "pnl": pnl}},
        "bars": {"08:04": b0804[["Open", "High", "Low", "Close", "Volume"]].to_dict("records"),
                 "08:22": b0822[["Open", "High", "Low", "Close", "Volume"]].to_dict(),
                 "exit_bar": ex.to_dict(),
                 "skipped_bad_ticks": [bt.isoformat() for bt in res.skipped_bad_ticks]},
        "cash_after": cash0, "day_pnl_after": day_pnl, "halted": halted})
    with open(rs_path, "w") as f:
        json.dump(rs, f, indent=2, default=str)

    print(json.dumps({"entry": entry, "qty": qty, "stop": stop, "target": target,
                      "exit_bar": ex.to_dict(), "exit": exit_px, "proceeds": proceeds, "pnl": pnl,
                      "cash": cash0, "halted": halted}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
