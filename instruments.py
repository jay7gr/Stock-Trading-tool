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
"""

from __future__ import annotations

from dataclasses import dataclass, field


class InstrumentMismatchError(ValueError):
    """Raised when a ticket and a fill do not refer to the same instrument."""


class UnknownInstrumentError(KeyError):
    """Raised when a symbol is not in the registry (no guessing, no aliases)."""


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

    def to_major(self, raw_price: float) -> float:
        """Vendor quote -> major currency units (GBX pence -> GBP pounds)."""
        if raw_price is None:
            return None
        return raw_price / 100.0 if self.quote_unit == "GBX" else float(raw_price)

    def to_book_gbp(self, raw_price: float, gbpusd: float | None = None) -> float:
        """Vendor quote -> GBP for the paper book."""
        major = self.to_major(raw_price)
        if major is None:
            return None
        if self.currency == "GBP":
            return major
        if self.currency == "USD":
            if not gbpusd or gbpusd <= 0:
                raise ValueError(f"{self.symbol} trades in USD; GBPUSD rate required to book in GBP")
            return major / gbpusd
        raise ValueError(f"Unsupported currency {self.currency} for {self.symbol}")


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
}

# Exact yfinance-symbol index (1:1, built from the registry; not aliases).
_BY_YF: dict[str, Instrument] = {i.yf_symbol.upper(): i for i in REGISTRY.values()}
assert len(_BY_YF) == len(REGISTRY), "duplicate yf_symbol in registry"
assert len({i.isin for i in REGISTRY.values()}) == len(REGISTRY), "duplicate ISIN in registry"


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
    return fill
