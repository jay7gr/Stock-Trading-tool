"""
Paper Trading Emulator — simulates trade execution without real money.

Tracks positions, fills, P&L, and trade history as if connected to a real broker.
Uses real market prices from yfinance.

Post-2026-09-25 safeguards:
  * Stops/targets are checked against 1m bar High/Low since the later of
    opened_at and last_check (bar_checks.py), not against a single mark.
  * Every fill uses a quote no older than quotes.MAX_QUOTE_AGE_S (60s) at
    fill time; quote_ts/quote_source are stored on each Trade. Stale quotes
    are re-quoted once, then rejected (StaleQuoteError).
  * Every ticket/fill resolves through instruments.py and must match the
    ticket's ISIN, listing and currency (InstrumentMismatchError otherwise).
  * Risk SOP v2.2: fills need exchange session data, an open market and a
    live stop/target monitor heartbeat (MarketNotCoveredError otherwise).
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, date, timedelta
from typing import Callable, Optional

import config
from market_data import get_current_price
import instruments
from instruments import InstrumentMismatchError, UnknownInstrumentError, NoSessionDataError
import market_hours
from market_hours import MarketNotCoveredError
import quotes
from quotes import Quote, StaleQuoteError, now_london, to_london
from bar_checks import check_position, floor_minute
from alerts import append_alert


def _to_book_gbp(ticker: str, raw_price: float, reference_gbp: float | None = None) -> float:
    """Normalise vendor quotes into GBP for the paper book.

    Yahoo LSE (.L) last prices are usually GBp (pence). Our book stores GBP.
    If a .L quote looks like pence vs the position's GBP entry, convert.
    """
    if raw_price is None:
        return raw_price
    if ticker.endswith(".L"):
        # Typical LSE ETF/stock: GBp quote is ~100x the GBP price.
        if reference_gbp and reference_gbp > 0 and raw_price > reference_gbp * 20:
            return raw_price / 100.0
        if reference_gbp is None and raw_price >= 50:
            # No reference: assume GBp for .L when quote looks like pence.
            return raw_price / 100.0
    return raw_price

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
LIVE_STATUS_FILE = os.path.join(DATA_DIR, "live_status.json")
FX_MAX_AGE_S = 300


def _atomic_write_json(path: str, obj) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


@dataclass
class Trade:
    id: str
    ticker: str
    action: str          # BUY or SELL
    quantity: float
    price: float
    value_gbp: float
    timestamp: str
    reasoning: str
    claude_score: float
    grok_score: float
    combined_score: float
    stop_loss: float
    take_profit: float
    status: str = "open"  # open, closed, stopped_out, take_profit_hit
    close_price: float = 0.0
    close_timestamp: str = ""
    pnl: float = 0.0
    # Added 2026-09-25 (backward-compatible defaults for older trades.json rows)
    quote_ts: str = ""        # timestamp of the quote/bar the fill price came from
    quote_source: str = ""    # e.g. yfinance_chart_regularMarketTime, yfinance_1m_bar
    isin: str = ""
    ticket_symbol: str = ""


@dataclass
class Position:
    ticker: str
    quantity: float
    avg_entry_price: float
    current_price: float
    value_gbp: float
    unrealised_pnl: float
    unrealised_pnl_pct: float
    stop_loss: float
    take_profit: float
    opened_at: str
    last_check: str = ""      # end of the last 1m bar checked for stop/target
    isin: str = ""
    ticket_symbol: str = ""


_TRADE_FIELDS = {f.name for f in fields(Trade)}


class PaperTradingEmulator:
    def __init__(self, data_dir: Optional[str] = None,
                 quote_fn: Optional[Callable[[str], Optional[Quote]]] = None,
                 bars_fn: Optional[Callable] = None,
                 now_fn: Optional[Callable[[], datetime]] = None,
                 write_alerts: bool = True):
        self.data_dir = data_dir or DATA_DIR
        self.trades_file = TRADES_FILE if data_dir is None else os.path.join(data_dir, "trades.json")
        self.portfolio_file = PORTFOLIO_FILE if data_dir is None else os.path.join(data_dir, "portfolio.json")
        self.live_status_file = LIVE_STATUS_FILE if data_dir is None else os.path.join(data_dir, "live_status.json")
        self.quote_fn = quote_fn or quotes.fetch_quote
        self.bars_fn = bars_fn or quotes.fetch_bars
        self.now_fn = now_fn or now_london
        self.write_alerts = write_alerts
        self.requote_wait_s = 5.0
        self.last_events: list[dict] = []
        self._fx_cache: dict = {}  # ccy -> (rate, fetched_monotonic)
        self.cash = config.INITIAL_CAPITAL
        self.positions: dict[str, Position] = {}
        self.trade_history: list[Trade] = []
        self.trade_counter = 0
        self._ensure_data_dir()
        self._load_state()

    def _ensure_data_dir(self):
        os.makedirs(self.data_dir, exist_ok=True)

    # ─── helpers ──────────────────────────────────────────────────────

    def _alert(self, type_: str, ticker: str, details: dict, event_ts=None, detected_ts=None):
        rec = {"type": type_, "ticker": ticker, "details": details,
               "event_ts": event_ts, "detected_ts": detected_ts}
        self.last_events.append(rec)
        if self.write_alerts:
            try:
                append_alert(self.data_dir, type_, ticker, details,
                             event_ts=event_ts, detected_ts=detected_ts)
            except Exception as e:  # noqa: BLE001
                print(f"[Emulator] alert log failed: {e}")

    def _fx(self, ccy: str) -> float:
        """Units of `ccy` per 1 GBP (GBP{ccy}=X), cached 60s per currency."""
        cache = self._fx_cache if isinstance(self._fx_cache, dict) else {}
        hit = cache.get(ccy)
        if hit and time.monotonic() - hit[1] < 60:
            return hit[0]
        q = quotes.get_fresh_quote(f"GBP{ccy}=X", at=to_london(self.now_fn()), max_age_s=FX_MAX_AGE_S,
                                   fetch=self.quote_fn, requote_wait_s=self.requote_wait_s)
        cache[ccy] = (float(q.price), time.monotonic())
        self._fx_cache = cache
        return cache[ccy][0]

    def _gbpusd(self) -> float:
        return self._fx("USD")

    def _book_price(self, ticker: str, raw: float, reference_gbp: Optional[float] = None) -> float:
        """Vendor quote -> GBP book price. Registry first (knows GBX vs GBP vs USD);
        unregistered tickers fall back to the _to_book_gbp heuristic."""
        if raw is None:
            return None
        inst = instruments.lookup(ticker)
        if inst is None:
            return _to_book_gbp(ticker, raw, reference_gbp)
        if inst.currency != "GBP":
            return inst.to_book_gbp(raw, fx=self._fx(inst.currency))
        return inst.to_book_gbp(raw)

    def _read_live_status(self) -> dict:
        try:
            with open(self.live_status_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def is_halted_today(self, now: Optional[datetime] = None) -> bool:
        ls = self._read_live_status()
        if not ls.get("halted"):
            return False
        today = to_london(now or self.now_fn()).date().isoformat()
        hd = ls.get("halted_date") or str(ls.get("as_of", ""))[:10]
        return hd == today

    # ─── fills ────────────────────────────────────────────────────────

    def execute_buy(self, ticker: str, size_gbp: float, price: float,
                    stop_loss: float, take_profit: float,
                    reasoning: str, claude_score: float, grok_score: float,
                    combined_score: float, ticket_symbol: Optional[str] = None,
                    quote: Optional[Quote] = None,
                    ticket_isin: Optional[str] = None,
                    ticket_listing: Optional[str] = None,
                    ticket_currency: Optional[str] = None) -> Optional[Trade]:
        """Execute a paper buy order.

        `ticket_symbol` is what the ticket/plan named (defaults to `ticker`);
        the fill instrument must match it in the registry. The fill price is
        taken from a quote no older than 60s (re-quoted if needed); `price`
        is the caller's reference price only.
        """
        now = to_london(self.now_fn())
        ticket_symbol = ticket_symbol or ticker
        try:
            inst = instruments.assert_fill_matches_ticket(
                ticket_symbol, ticker, ticket_isin, ticket_listing, ticket_currency)
        except (InstrumentMismatchError, UnknownInstrumentError, NoSessionDataError) as e:
            self._alert("skip", ticker, {"reason": "instrument_check_failed",
                                         "ticket": ticket_symbol, "error": str(e)})
            raise
        # Risk SOP v2.2: market must be open and its hours covered by the live monitor.
        try:
            market_hours.require_fill_coverage(inst.symbol, at=now, data_dir=self.data_dir)
        except MarketNotCoveredError as e:
            self._alert("skip", ticker, {"reason": "market_not_covered", "error": str(e)})
            raise
        if quote is not None and quote.symbol.upper() != inst.yf_symbol.upper():
            err = InstrumentMismatchError(
                f"quote symbol {quote.symbol} is not {inst.yf_symbol} ({inst.isin})")
            self._alert("skip", ticker, {"reason": "quote_instrument_mismatch", "error": str(err)})
            raise err
        if self.is_halted_today(now):
            self._alert("skip", ticker, {"reason": "day_halted", "ticket": ticket_symbol})
            return None

        if size_gbp > self.cash:
            size_gbp = self.cash  # Use available cash
        if size_gbp < config.MIN_POSITION_SIZE:
            return None

        try:
            q = quotes.get_fresh_quote(inst.yf_symbol, at=now, fetch=self.quote_fn,
                                       existing=quote, requote_wait_s=self.requote_wait_s)
        except StaleQuoteError as e:
            self._alert("skip", ticker, {"reason": "stale_quote", "error": str(e),
                                         "ref_price": price})
            raise
        fill_price = self._book_price(inst.yf_symbol, q.price)
        note = ""
        if price and fill_price and abs(fill_price / price - 1) > 0.005:
            note = (f" [fill {fill_price:.4f} from fresh quote vs ref {price:.4f} "
                    f"({(fill_price / price - 1) * 100:+.2f}%)]")

        key = inst.yf_symbol
        quantity = size_gbp / fill_price
        self.cash -= size_gbp
        self.trade_counter += 1

        trade = Trade(
            id=f"T{self.trade_counter:05d}",
            ticker=key, action="BUY",
            quantity=quantity, price=fill_price, value_gbp=size_gbp,
            timestamp=now.isoformat(),
            reasoning=reasoning + note,
            claude_score=claude_score, grok_score=grok_score,
            combined_score=combined_score,
            stop_loss=stop_loss, take_profit=take_profit,
            quote_ts=to_london(q.ts).isoformat(), quote_source=q.source,
            isin=inst.isin, ticket_symbol=inst.symbol,
        )

        # Update or create position
        if key in self.positions:
            pos = self.positions[key]
            total_qty = pos.quantity + quantity
            pos.avg_entry_price = (
                (pos.avg_entry_price * pos.quantity + fill_price * quantity) / total_qty
            )
            pos.quantity = total_qty
            pos.stop_loss = stop_loss
            pos.take_profit = take_profit
        else:
            self.positions[key] = Position(
                ticker=key, quantity=quantity,
                avg_entry_price=fill_price, current_price=fill_price,
                value_gbp=size_gbp, unrealised_pnl=0, unrealised_pnl_pct=0,
                stop_loss=stop_loss, take_profit=take_profit,
                opened_at=now.isoformat(), last_check="",
                isin=inst.isin, ticket_symbol=inst.symbol,
            )

        self.trade_history.append(trade)
        self._save_state()
        self._alert("fill", key, {"trade_id": trade.id, "side": "BUY", "price": fill_price,
                                  "qty": quantity, "value_gbp": size_gbp,
                                  "stop": stop_loss, "target": take_profit,
                                  "quote_ts": trade.quote_ts, "quote_source": q.source,
                                  "isin": inst.isin},
                    event_ts=now, detected_ts=now)
        return trade

    def execute_sell(self, ticker: str, reason: str = "signal",
                     fill_price: Optional[float] = None, fill_ts=None,
                     quote_ts=None, quote_source: Optional[str] = None,
                     status: str = "closed") -> Optional[Trade]:
        """Sell entire position in a ticker.

        Bar-based exits pass fill_price/fill_ts (the triggering 1m bar, so the
        quote is the fill's own bar). Otherwise a fresh (<=60s) quote is required.
        """
        if ticker not in self.positions:
            return None

        pos = self.positions[ticker]
        now = to_london(self.now_fn())
        if fill_price is None:
            inst = instruments.lookup(ticker)
            yf_sym = inst.yf_symbol if inst else ticker
            try:
                q = quotes.get_fresh_quote(yf_sym, at=now, fetch=self.quote_fn,
                                           requote_wait_s=self.requote_wait_s)
            except StaleQuoteError as e:
                self._alert("skip", ticker, {"reason": "stale_quote_on_sell", "error": str(e)})
                raise
            fill_price = self._book_price(ticker, q.price, pos.avg_entry_price)
            fill_ts = now
            quote_ts = q.ts
            quote_source = q.source
        fill_ts = to_london(fill_ts) if fill_ts else now
        quote_ts = to_london(quote_ts) if quote_ts else fill_ts

        sell_value = pos.quantity * fill_price
        pnl = sell_value - (pos.quantity * pos.avg_entry_price)

        self.cash += sell_value
        self.trade_counter += 1

        trade = Trade(
            id=f"T{self.trade_counter:05d}",
            ticker=ticker, action="SELL",
            quantity=pos.quantity, price=fill_price, value_gbp=sell_value,
            timestamp=fill_ts.isoformat(),
            reasoning=f"SELL — {reason}",
            claude_score=0, grok_score=0, combined_score=0,
            stop_loss=0, take_profit=0,
            status=status, close_price=fill_price,
            close_timestamp=fill_ts.isoformat(),
            pnl=pnl,
            quote_ts=quote_ts.isoformat(), quote_source=quote_source or "",
            isin=pos.isin, ticket_symbol=pos.ticket_symbol,
        )

        # Mark the open BUY leg(s) as closed so history reads consistently.
        for t in self.trade_history:
            if t.ticker == ticker and t.action == "BUY" and t.status == "open":
                t.status = status
                t.close_price = fill_price
                t.close_timestamp = fill_ts.isoformat()
                t.pnl = t.quantity * (fill_price - t.price)

        del self.positions[ticker]
        self.trade_history.append(trade)
        self._save_state()
        return trade

    def check_stops_and_targets(self, now: Optional[datetime] = None,
                                tickers: Optional[list] = None) -> list[Trade]:
        """Check all positions for stop-loss / take-profit hits using 1m bars.

        For each position, pull 1m bars since max(floor(opened_at), last_check).
        First bar with Low <= stop exits at min(stop, Open); first bar with
        High >= target exits at target; stop wins if both hit in one bar.
        """
        now = to_london(now or self.now_fn())
        closed_trades = []
        exits = []

        for ticker, pos in self.positions.items():
            if tickers is not None and ticker not in tickers:
                continue   # market closed: keep last mark, no network
            inst = instruments.lookup(ticker)
            yf_sym = inst.yf_symbol if inst else ticker
            since = floor_minute(to_london(pos.opened_at)) if pos.opened_at else now
            if pos.last_check and to_london(pos.last_check) > since:
                since = to_london(pos.last_check)
            try:
                # 10 min of lookback gives the bad-tick guard a previous close
                bars = self.bars_fn(yf_sym, since - timedelta(minutes=10), now)
                res = check_position(
                    bars, stop=pos.stop_loss, target=pos.take_profit,
                    opened_at=pos.opened_at or now, last_check=pos.last_check or None,
                    now=now,
                    to_book=lambda x, _t=yf_sym, _r=pos.avg_entry_price: self._book_price(_t, x, _r),
                )
            except Exception as e:  # noqa: BLE001
                print(f"[Emulator] bar check failed for {ticker}: {e}")
                continue
            for bt in res.skipped_bad_ticks:
                self._alert("skip", ticker, {"reason": "bad_tick_unconfirmed_by_next_bar",
                                             "bar_ts": bt.isoformat(), "stop": pos.stop_loss,
                                             "target": pos.take_profit},
                            event_ts=bt, detected_ts=now)
            if res.exit:
                exits.append((ticker, res))
                continue
            if res.last_check:
                pos.last_check = res.last_check.isoformat()
            if res.last_close is not None:
                pos.current_price = res.last_close
                pos.value_gbp = pos.quantity * res.last_close
                cost = pos.quantity * pos.avg_entry_price
                pos.unrealised_pnl = pos.value_gbp - cost
                pos.unrealised_pnl_pct = pos.unrealised_pnl / cost * 100 if cost else 0

        for ticker, res in exits:
            ex = res.exit
            pos = self.positions[ticker]
            if ex.kind == "stop":
                status = "stopped_out"
                reason = (f"stopped_out: 1m bar {ex.bar_ts:%Y-%m-%d %H:%M} Low {ex.bar_low:.4f} "
                          f"<= stop {ex.level:.4f}; exit {ex.price:.4f}"
                          + (" (gapped: filled at bar Open)" if ex.gapped else " (at stop)"))
            else:
                status = "take_profit_hit"
                reason = (f"take_profit_hit: 1m bar {ex.bar_ts:%Y-%m-%d %H:%M} High {ex.bar_high:.4f} "
                          f">= target {ex.level:.4f}; exit at target")
            entry = pos.avg_entry_price
            trade = self.execute_sell(ticker, reason, fill_price=ex.price, fill_ts=ex.bar_ts,
                                      quote_ts=ex.bar_ts, quote_source="yfinance_1m_bar",
                                      status=status)
            if trade:
                closed_trades.append(trade)
                self._alert(ex.kind, ticker,
                            {"trade_id": trade.id, "entry": entry, "exit": ex.price,
                             "level": ex.level, "gapped": ex.gapped, "pnl": trade.pnl,
                             "bar": ex.to_dict()},
                            event_ts=ex.bar_ts, detected_ts=now)

        self._save_state()
        return closed_trades

    def _update_positions(self):
        """Update current prices and unrealised P&L for all positions."""
        for ticker, pos in self.positions.items():
            raw = get_current_price(ticker)
            if raw:
                try:
                    price = self._book_price(ticker, raw, pos.avg_entry_price)
                except Exception:  # noqa: BLE001  (e.g. FX unavailable)
                    continue
                pos.current_price = price
                pos.value_gbp = pos.quantity * price
                cost_basis = pos.quantity * pos.avg_entry_price
                pos.unrealised_pnl = pos.value_gbp - cost_basis
                pos.unrealised_pnl_pct = (
                    (pos.unrealised_pnl / cost_basis * 100) if cost_basis > 0 else 0
                )

    def portfolio_value(self) -> float:
        """Total portfolio value = cash + positions."""
        self._update_positions()
        positions_value = sum(p.value_gbp for p in self.positions.values())
        return self.cash + positions_value

    def total_unrealised_pnl(self) -> float:
        return sum(p.unrealised_pnl for p in self.positions.values())

    def total_realised_pnl(self) -> float:
        return sum(t.pnl for t in self.trade_history if t.action == "SELL")

    def total_pnl(self) -> float:
        return self.total_realised_pnl() + self.total_unrealised_pnl()

    def daily_trades(self, d: Optional[date] = None) -> list[Trade]:
        d = d or date.today()
        return [
            t for t in self.trade_history
            if t.timestamp[:10] == d.isoformat()
        ]

    def daily_pnl(self, d: Optional[date] = None) -> float:
        trades = self.daily_trades(d)
        return sum(t.pnl for t in trades if t.action == "SELL")

    def get_positions_dict(self) -> dict:
        """Return positions as dict for risk manager compatibility."""
        return {
            ticker: {"value": pos.value_gbp, "quantity": pos.quantity}
            for ticker, pos in self.positions.items()
        }

    def get_summary(self) -> dict:
        pv = self.portfolio_value()
        return {
            "cash": self.cash,
            "positions_value": pv - self.cash,
            "portfolio_value": pv,
            "total_pnl": self.total_pnl(),
            "total_pnl_pct": (pv / config.INITIAL_CAPITAL - 1) * 100,
            "realised_pnl": self.total_realised_pnl(),
            "unrealised_pnl": self.total_unrealised_pnl(),
            "open_positions": len(self.positions),
            "total_trades": len(self.trade_history),
        }

    # ─── Persistence ──────────────────────────────────────────────────

    def _save_state(self):
        state = {
            "cash": self.cash,
            "trade_counter": self.trade_counter,
            "positions": {
                t: {
                    "ticker": p.ticker, "quantity": p.quantity,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                    "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                    "opened_at": p.opened_at,
                    "last_check": p.last_check,
                    "isin": p.isin, "ticket_symbol": p.ticket_symbol,
                }
                for t, p in self.positions.items()
            },
        }
        _atomic_write_json(self.portfolio_file, state)

        trades = [asdict(t) for t in self.trade_history]
        _atomic_write_json(self.trades_file, trades)

    def _load_state(self):
        if os.path.exists(self.portfolio_file):
            try:
                with open(self.portfolio_file) as f:
                    state = json.load(f)
                self.cash = state.get("cash", config.INITIAL_CAPITAL)
                self.trade_counter = state.get("trade_counter", 0)
                for t, p in state.get("positions", {}).items():
                    self.positions[t] = Position(
                        ticker=p["ticker"], quantity=p["quantity"],
                        avg_entry_price=p["avg_entry_price"],
                        current_price=p.get("current_price", p["avg_entry_price"]),
                        value_gbp=p["quantity"] * p.get("current_price", p["avg_entry_price"]),
                        unrealised_pnl=0, unrealised_pnl_pct=0,
                        stop_loss=p.get("stop_loss", 0),
                        take_profit=p.get("take_profit", 0),
                        opened_at=p.get("opened_at", ""),
                        last_check=p.get("last_check", ""),
                        isin=p.get("isin", ""),
                        ticket_symbol=p.get("ticket_symbol", ""),
                    )
            except (json.JSONDecodeError, KeyError):
                pass

        if os.path.exists(self.trades_file):
            try:
                with open(self.trades_file) as f:
                    trades = json.load(f)
                # Ignore unknown keys so newer/older trades.json rows both load.
                self.trade_history = [
                    Trade(**{k: v for k, v in t.items() if k in _TRADE_FIELDS})
                    for t in trades
                ]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
