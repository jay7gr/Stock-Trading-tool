"""
Instrument registry — single source of truth for instrument identity.

Every ticket (the symbol a trade plan/ticket names) and every fill must
resolve through this registry. Fills assert that the filled instrument's
ISIN, listing and trading currency match the ticket's. This exists because
on 2026-09-25 a ticket for CIBR (First Trust Nasdaq Cybersecurity UCITS,
IE00BF16M727, LSE USD line) was filled as ISPY.L (L&G Cyber Security
UCITS, IE00BYPLS672, LSE GBX line) through a ticker alias.

There are deliberately NO aliases: a ticket symbol maps to exactly one
listing. Different ISINs never resolve to each other.

ISIN sources (checked 2026-09-25):
  CIBR  IE00BF16M727  First Trust factsheet (api.fundinfo.com, 2026-05-29):
        "London Stock Exchange USD CIBR LN BL6LC29"; justETF IE00BF16M727
        (LSE USD = CIBR, LSE GBX = FCBR); Freetrade page GB/CIBR ($, USD).
  ISPY  IE00BYPLS672  L&G KID (api.fundinfo.com, 2025-02-19); justETF
        IE00BYPLS672 (LSE GBX = ISPY, LSE USD = USPY); Trading 212 ISPY.GB.
  IESU  IE00B42NKQ00  iShares product page 280503 (LSE GBP line IESU,
        SEDOL B45CJS9, RIC IESU.L); FT.com IESU:LSE:GBX; justETF.
  VUSA  IE00B3XXRP09  Vanguard factsheet 9503 (LSE GBP VUSA, SEDOL
        B7NLLS3, RIC VUSA.L); justETF IE00B3XXRP09.
  XOM   US30233Q1085  ISIN changed from US30231G1022 on 2026-07-08 when
        Exxon Mobil Corp became ExxonMobil Holdings Corp (Wiener Börse
        notice 2026-07-08, KASE 2026-07-08, Eurex corporate action
        2026-07-13). Freetrade US/XOM shows "ExxonMobil Holdings Corp".
Quote units cross-checked against yfinance fast_info.currency on
2026-09-25: CIBR.L=USD, ISPY.L=GBp, IESU.L=GBp, VUSA.L=GBP, XOM=USD.

Monday 28 Sep 2026 shortlist (checked 2026-09-27; OpenFIGI v3 /mapping by
ISIN, Freetrade public universe pages, yfinance chart metadata):
  TSM   US8740391003  OpenFIGI BBG000BD8ZK0 "TAIWAN SEMICONDUCTOR-SP ADR"
        (ADR, US); yfinance TSM exchangeName NYQ (NYSE), USD; Freetrade US/TSM
        "Taiwan Semiconductor Manufacturing Company Ltd (ADR)".
  SHEL  GB00BP6MXD84  OpenFIGI BBG0149N4YC8 "SHELL PLC" LN; yfinance SHEL.L
        LSE, currency GBp (pence); Freetrade GB/SHEL "Shell Plc" (£36.14).
        NB Yahoo "SHEL" is the NYSE ADR (USD, ~$95.78): NOT this instrument.
        The ticket symbol SHEL resolves to the LSE line (yf SHEL.L) only.
        UK stamp duty 0.5% applies on purchase (Risk ticket, SOP v2.3).
  WMT   US9311421039  OpenFIGI BBG000BWXBC2 "WALMART INC"; listing moved
        NYSE -> Nasdaq Global Select on 2025-12-09 (Walmart/Nasdaq press
        releases 2025-11-20 and 2025-12-09; 8-K). yfinance WMT exchangeName
        NMS (NasdaqGS), USD; Freetrade US/WMT "Walmart Inc".
  MSFT  US5949181045  OpenFIGI BBG000BPH459 "MICROSOFT CORP"; yfinance NMS
        (NasdaqGS), USD; Freetrade US/MSFT "Microsoft Corp".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo


class InstrumentMismatchError(ValueError):
    """Raised when a ticket and a fill do not refer to the same instrument."""


class UnknownInstrumentError(KeyError):
    """Raised when a symbol is not in the registry (no guessing, no aliases)."""


class NoSessionDataError(ValueError):
    """Raised when an instrument's exchange has no trading-session data
    (Risk SOP v2.2: such instruments cannot be registered or filled)."""


# ─── Exchanges & trading sessions (Risk SOP v2.2) ─────────────────────
#
# Times are exchange-local wall-clock; zoneinfo handles DST per exchange.
# `segments` are the continuous-trading periods; `close_end` is when the
# closing auction/fixing finishes (the last moment a print can occur).
# The monitor watches [first segment open, close_end] on local weekdays
# (lunch breaks included — harmless, and safer than missing a print) and
# runs one final check within `final_check_min` minutes after close_end.
# Exchange holidays and half-days are NOT modelled (monitor just checks on
# a closed day; empty bars are harmless).
# Sources checked 2026-09-25: JPX trading-hours page (TSE 09:00-11:30,
# 12:30-15:30, closing auction 15:25-15:30); HKEX securities-market hours
# (09:30-12:00, extended morning 12:00-13:00, 13:00-16:00, CAS to 16:10);
# ASX cash-market phases (open ~10:00, normal trading to 16:00, CSPA to
# 16:11). LSE/Xetra/Euronext/NYSE/Nasdaq are the standard published hours.

@dataclass(frozen=True)
class Exchange:
    code: str
    name: str
    tz: str
    segments: tuple            # (("HH:MM","HH:MM"), ...) local continuous trading
    close_end: str             # local time the closing auction ends
    final_check_min: int = 10
    weekdays: tuple = (0, 1, 2, 3, 4)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @staticmethod
    def _t(hhmm: str) -> dtime:
        h, m = hhmm.split(":")
        return dtime(int(h), int(m))

    def window_on(self, local_day: date):
        """(open, close_end) as aware datetimes for a local date, or None if not a trading weekday."""
        if local_day.weekday() not in self.weekdays or not self.segments:
            return None
        z = self.zone
        o = datetime.combine(local_day, self._t(self.segments[0][0]), tzinfo=z)
        c = datetime.combine(local_day, self._t(self.close_end), tzinfo=z)
        return o, c

    def _aware(self, at: datetime) -> datetime:
        if at.tzinfo is None:
            raise ValueError("timezone-aware datetime required")
        return at.astimezone(self.zone)

    def state(self, at: datetime):
        """('open'|'final'|'closed', window) at instant `at`.
        'final' = within final_check_min after close_end of today's session."""
        loc = self._aware(at)
        for d in (loc.date(), loc.date() - timedelta(days=1)):
            w = self.window_on(d)
            if not w:
                continue
            o, c = w
            if o <= loc <= c:
                return "open", w
            if c < loc <= c + timedelta(minutes=self.final_check_min):
                return "final", w
        return "closed", None

    def is_open(self, at: datetime) -> bool:
        return self.state(at)[0] == "open"

    def next_open(self, at: datetime) -> datetime:
        loc = self._aware(at)
        for i in range(0, 8):
            w = self.window_on(loc.date() + timedelta(days=i))
            if w and w[0] > loc:
                return w[0]
        raise RuntimeError(f"no session found within a week for {self.code}")


EXCHANGES: dict[str, Exchange] = {
    "LSE": Exchange("LSE", "London Stock Exchange", "Europe/London",
                    (("08:00", "16:30"),), close_end="16:35"),
    "XETRA": Exchange("XETRA", "Deutsche Boerse Xetra", "Europe/Berlin",
                      (("09:00", "17:30"),), close_end="17:35"),
    "EURONEXT_PARIS": Exchange("EURONEXT_PARIS", "Euronext Paris", "Europe/Paris",
                               (("09:00", "17:30"),), close_end="17:35"),
    "EURONEXT_AMSTERDAM": Exchange("EURONEXT_AMSTERDAM", "Euronext Amsterdam", "Europe/Amsterdam",
                                   (("09:00", "17:30"),), close_end="17:35"),
    "NYSE": Exchange("NYSE", "New York Stock Exchange", "America/New_York",
                     (("09:30", "16:00"),), close_end="16:00"),
    "NASDAQ": Exchange("NASDAQ", "Nasdaq", "America/New_York",
                       (("09:30", "16:00"),), close_end="16:00"),
    "TSE": Exchange("TSE", "Tokyo Stock Exchange", "Asia/Tokyo",
                    (("09:00", "11:30"), ("12:30", "15:30")), close_end="15:30"),
    "HKEX": Exchange("HKEX", "Hong Kong Exchanges", "Asia/Hong_Kong",
                     (("09:30", "12:00"), ("12:00", "13:00"), ("13:00", "16:00")), close_end="16:10"),
    "ASX": Exchange("ASX", "Australian Securities Exchange", "Australia/Sydney",
                    (("10:00", "16:00"),), close_end="16:12"),
}


def exchange_of(inst: "Instrument") -> Exchange:
    ex = EXCHANGES.get(inst.exchange)
    if ex is None or not ex.segments:
        raise NoSessionDataError(
            f"{inst.symbol} ({inst.isin}) lists on {inst.exchange!r}, which has no session data")
    return ex


def require_session(inst: "Instrument") -> Exchange:
    return exchange_of(inst)


@dataclass(frozen=True)
class Instrument:
    symbol: str            # ticket symbol (what plans/tickets name)
    name: str
    isin: str
    exchange: str          # listing venue, e.g. "LSE", "NYSE"
    listing: str           # venue + trading line, e.g. "LSE:USD"
    currency: str          # trading currency of this line: GBP / USD
    quote_unit: str        # unit the vendor quotes in: GBX / GBP / USD
    yf_symbol: str         # yfinance symbol
    freetrade_ticker: str
    isin_verified: bool = True
    freetrade_verified: bool = True
    former_isins: tuple = field(default_factory=tuple)
    sources: tuple = field(default_factory=tuple)
    purchase_tax_pct: float = 0.0   # e.g. UK stamp duty 0.005 on UK shares (not ETFs)

    def to_major(self, raw_price: float) -> float:
        """Vendor quote -> major currency units (GBX pence -> GBP pounds)."""
        if raw_price is None:
            return None
        return raw_price / 100.0 if self.quote_unit == "GBX" else float(raw_price)

    def to_book_gbp(self, raw_price: float, gbpusd: float | None = None,
                    fx: float | None = None) -> float:
        """Vendor quote -> GBP for the paper book.
        Non-GBP lines need `fx` = units of the trading currency per 1 GBP
        (e.g. GBPUSD=X, GBPJPY=X); `gbpusd` is kept for backward compatibility."""
        major = self.to_major(raw_price)
        if major is None:
            return None
        if self.currency == "GBP":
            return major
        rate = fx or gbpusd
        if not rate or rate <= 0:
            raise ValueError(f"{self.symbol} trades in {self.currency}; GBP{self.currency} rate required to book in GBP")
        return major / rate


REGISTRY: dict[str, Instrument] = {
    "CIBR": Instrument(
        symbol="CIBR",
        name="First Trust Nasdaq Cybersecurity UCITS ETF Acc (USD line)",
        isin="IE00BF16M727", exchange="LSE", listing="LSE:USD",
        currency="USD", quote_unit="USD",
        yf_symbol="CIBR.L", freetrade_ticker="CIBR",
        sources=(
            "https://api.fundinfo.com/document/1043043916cb996f6581bf7515163a6a_90796/MR_DE_en_IE00BF16M727_RES_2026-05-29.pdf",
            "https://www.justetf.com/uk/etf-profile.html?isin=IE00BF16M727",
            "https://web.freetrade.io/universe/GB/CIBR",
        ),
    ),
    "ISPY": Instrument(
        symbol="ISPY",
        name="L&G Cyber Security UCITS ETF (GBX line)",
        isin="IE00BYPLS672", exchange="LSE", listing="LSE:GBX",
        currency="GBP", quote_unit="GBX",
        yf_symbol="ISPY.L", freetrade_ticker="ISPY",
        sources=(
            "https://api.fundinfo.com/document/2abf1720c1ae9aa7f4ce6aba672d7851_105005/KID_GB_en_IE00BYPLS672_YES_2025-02-19.pdf",
            "https://www.justetf.com/en/etf-profile.html?isin=IE00BYPLS672",
            "https://www.trading212.com/trading-instruments/invest/ISPY.GB",
            "https://web.freetrade.io/universe/GB/ISPY",
        ),
    ),
    "IESU": Instrument(
        symbol="IESU",
        name="iShares S&P 500 Energy Sector UCITS ETF Acc (GBX line)",
        isin="IE00B42NKQ00", exchange="LSE", listing="LSE:GBX",
        currency="GBP", quote_unit="GBX",
        yf_symbol="IESU.L", freetrade_ticker="IESU",
        sources=(
            "https://www.ishares.com/uk/individual/en/products/280503",
            "https://markets.ft.com/data/etfs/tearsheet/summary?s=IESU%3ALSE%3AGBX",
            "https://www.justetf.com/uk/etf-profile.html?isin=IE00B42NKQ00",
            "https://web.freetrade.io/universe/GB/IESU",
        ),
    ),
    "VUSA": Instrument(
        symbol="VUSA",
        name="Vanguard S&P 500 UCITS ETF (USD) Distributing (GBP line)",
        isin="IE00B3XXRP09", exchange="LSE", listing="LSE:GBP",
        currency="GBP", quote_unit="GBP",   # NB: quoted in pounds, not pence
        yf_symbol="VUSA.L", freetrade_ticker="VUSA",
        sources=(
            "https://fund-docs.vanguard.com/SandP_500_UCITS_ETF_USD_Distributing_9503_EU_INT_UK_EN.pdf",
            "https://www.justetf.com/en/etf-profile.html?isin=IE00B3XXRP09",
            "https://web.freetrade.io/universe/GB/VUSA",
        ),
    ),
    "XOM": Instrument(
        symbol="XOM",
        name="ExxonMobil Holdings Corp",
        isin="US30233Q1085", exchange="NYSE", listing="NYSE:USD",
        currency="USD", quote_unit="USD",
        yf_symbol="XOM", freetrade_ticker="XOM",
        former_isins=("US30231G1022",),
        sources=(
            "https://wbag-prev-vse.factsetdigitalsolutions.com/en/news/vienna-stock-exchange-news/exxon-mobil-corp-change-of-isin-name-exxonmobil-holdings-corp/",
            "https://kase.kz/en/information/news/show/1570546",
            "https://www.eurex.com/ex-en/rules-regs/corporate-actions/corporate-action-information/Exxon-Mobil-Corporation-Name-Change-ISIN-Change-5377362",
            "https://web.freetrade.io/universe/US/XOM",
        ),
    ),
    "TSM": Instrument(
        symbol="TSM",
        name="Taiwan Semiconductor Manufacturing Co Ltd (ADR)",
        isin="US8740391003", exchange="NYSE", listing="NYSE:USD",
        currency="USD", quote_unit="USD",
        yf_symbol="TSM", freetrade_ticker="TSM",
        sources=(
            "https://api.openfigi.com/v3/mapping (ID_ISIN US8740391003 -> BBG000BD8ZK0, ADR, 2026-09-27)",
            "https://web.freetrade.io/universe/US/TSM",
        ),
    ),
    "SHEL": Instrument(
        symbol="SHEL",
        name="Shell plc (LSE ordinary shares, GBX line)",
        isin="GB00BP6MXD84", exchange="LSE", listing="LSE:GBX",
        currency="GBP", quote_unit="GBX",     # Yahoo SHEL.L quotes in pence (GBp)
        yf_symbol="SHEL.L", freetrade_ticker="SHEL",
        purchase_tax_pct=0.005,               # UK stamp duty (SDRT) on purchase
        sources=(
            "https://api.openfigi.com/v3/mapping (ID_ISIN GB00BP6MXD84 LN -> BBG0149N4YC8 SHELL PLC, 2026-09-27)",
            "https://web.freetrade.io/universe/GB/SHEL",
        ),
    ),
    "WMT": Instrument(
        symbol="WMT",
        name="Walmart Inc",
        isin="US9311421039", exchange="NASDAQ", listing="NASDAQ:USD",   # moved from NYSE 2025-12-09
        currency="USD", quote_unit="USD",
        yf_symbol="WMT", freetrade_ticker="WMT",
        sources=(
            "https://api.openfigi.com/v3/mapping (ID_ISIN US9311421039 -> BBG000BWXBC2 WALMART INC, 2026-09-27)",
            "https://www.nasdaq.com/press-release/walmart-debuts-nasdaq-marking-its-first-day-trading-2025-12-09",
            "https://web.freetrade.io/universe/US/WMT",
        ),
    ),
    "MSFT": Instrument(
        symbol="MSFT",
        name="Microsoft Corp",
        isin="US5949181045", exchange="NASDAQ", listing="NASDAQ:USD",
        currency="USD", quote_unit="USD",
        yf_symbol="MSFT", freetrade_ticker="MSFT",
        sources=(
            "https://api.openfigi.com/v3/mapping (ID_ISIN US5949181045 -> BBG000BPH459 MICROSOFT CORP, 2026-09-27)",
            "https://web.freetrade.io/universe/US/MSFT",
        ),
    ),
}

# Exact yfinance-symbol index (1:1, built from the registry; not aliases).
_BY_YF: dict[str, Instrument] = {i.yf_symbol.upper(): i for i in REGISTRY.values()}
assert len(_BY_YF) == len(REGISTRY), "duplicate yf_symbol in registry"
assert len({i.isin for i in REGISTRY.values()}) == len(REGISTRY), "duplicate ISIN in registry"
for _i in REGISTRY.values():
    require_session(_i)   # every built-in instrument must have session data


def register(inst: Instrument) -> Instrument:
    """Add an instrument (e.g. a new Asia pick). Rejects: missing session data,
    duplicate symbol / yf symbol / ISIN (no aliases)."""
    require_session(inst)
    key = inst.symbol.upper()
    if key in REGISTRY or inst.yf_symbol.upper() in _BY_YF:
        raise ValueError(f"{inst.symbol}/{inst.yf_symbol} already registered")
    if any(i.isin == inst.isin for i in REGISTRY.values()):
        raise ValueError(f"ISIN {inst.isin} already registered under another symbol")
    REGISTRY[key] = inst
    _BY_YF[inst.yf_symbol.upper()] = inst
    return inst


def unregister(symbol: str) -> None:
    inst = REGISTRY.pop(symbol.upper(), None)
    if inst:
        _BY_YF.pop(inst.yf_symbol.upper(), None)


def resolve(symbol: str) -> Instrument:
    """Resolve a ticket symbol (e.g. 'CIBR') or its exact yfinance symbol
    (e.g. 'CIBR.L') to one Instrument. Raises UnknownInstrumentError otherwise."""
    if not symbol:
        raise UnknownInstrumentError("empty symbol")
    key = symbol.strip().upper()
    if key in REGISTRY:
        return REGISTRY[key]
    if key in _BY_YF:
        return _BY_YF[key]
    raise UnknownInstrumentError(f"{symbol!r} is not in the instrument registry")


def lookup(symbol: str) -> Instrument | None:
    try:
        return resolve(symbol)
    except UnknownInstrumentError:
        return None


def assert_fill_matches_ticket(ticket_symbol: str, fill_symbol: str,
                               ticket_isin: str | None = None,
                               ticket_listing: str | None = None,
                               ticket_currency: str | None = None) -> Instrument:
    """Assert the fill instrument is the ticket instrument.

    ticket_* overrides let a ticket carry its own expected identity; when
    given they must also match the registry. Returns the resolved Instrument.
    """
    ticket = resolve(ticket_symbol)
    fill = resolve(fill_symbol)
    problems = []
    if ticket_isin and ticket_isin != ticket.isin:
        problems.append(f"ticket ISIN {ticket_isin} != registry {ticket.isin} for {ticket.symbol}")
    if ticket_listing and ticket_listing != ticket.listing:
        problems.append(f"ticket listing {ticket_listing} != registry {ticket.listing}")
    if ticket_currency and ticket_currency != ticket.currency:
        problems.append(f"ticket currency {ticket_currency} != registry {ticket.currency}")
    if fill.isin != ticket.isin:
        problems.append(f"ISIN mismatch: ticket {ticket.symbol} {ticket.isin} vs fill {fill.symbol} {fill.isin}")
    if fill.listing != ticket.listing:
        problems.append(f"listing mismatch: {ticket.listing} vs {fill.listing}")
    if fill.currency != ticket.currency:
        problems.append(f"currency mismatch: {ticket.currency} vs {fill.currency}")
    if problems:
        raise InstrumentMismatchError(
            f"ticket {ticket_symbol!r} cannot fill as {fill_symbol!r}: " + "; ".join(problems))
    require_session(fill)   # SOP v2.2: no session data -> no fill
    return fill
