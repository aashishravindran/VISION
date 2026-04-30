"""Technical indicators backed by Tiingo EOD prices and the `ta` library."""
import pandas as pd
from agents import function_tool
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from vision import cache
from vision.data import tiingo


def _load_ohlc(ticker: str, lookback_days: int) -> pd.DataFrame:
    rows = tiingo.get_price_history(ticker, lookback_days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([
        {
            "Date": r.get("date"),
            "Open": r.get("open"),
            "High": r.get("high"),
            "Low": r.get("low"),
            "Close": r.get("adjClose") or r.get("close"),
            "Volume": r.get("volume"),
        }
        for r in rows
    ])
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.dropna(subset=["Close"]).sort_values("Date").reset_index(drop=True)
    return df


def _compute_indicators(ticker: str, lookback_days: int = 365) -> dict:
    """Internal helper: compute indicators for a ticker. Used by both the
    compute_indicators tool and screen_universe (which calls it per ticker)."""
    params = {"ticker": ticker.upper(), "lookback_days": lookback_days}
    cached = cache.get("compute_indicators", params)
    if cached is not None:
        return cached

    df = _load_ohlc(ticker, lookback_days)
    if df.empty:
        return {"ticker": ticker.upper(), "error": "No price data from Tiingo for this ticker."}

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    sma_20 = SMAIndicator(close=close, window=20).sma_indicator()
    sma_50 = SMAIndicator(close=close, window=50).sma_indicator()
    sma_200 = SMAIndicator(close=close, window=200).sma_indicator()
    ema_20 = EMAIndicator(close=close, window=20).ema_indicator()
    rsi_14 = RSIIndicator(close=close, window=14).rsi()

    macd_obj = MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
    macd_line = macd_obj.macd()
    macd_signal = macd_obj.macd_signal()
    macd_hist = macd_obj.macd_diff()

    bb = BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_middle = bb.bollinger_mavg()
    bb_lower = bb.bollinger_lband()

    atr_14 = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    adx_14 = ADXIndicator(high=high, low=low, close=close, window=14).adx()

    def f(series_or_val):
        try:
            v = series_or_val.iloc[-1] if hasattr(series_or_val, "iloc") else series_or_val
            return round(float(v), 4) if pd.notna(v) else None
        except (TypeError, ValueError, IndexError):
            return None

    price = f(close)
    sma_50_v = f(sma_50)
    sma_200_v = f(sma_200)

    out = {
        "ticker": ticker.upper(),
        "as_of": str(df["Date"].iloc[-1].date()),
        "price": price,
        "sma_20": f(sma_20),
        "sma_50": sma_50_v,
        "sma_200": sma_200_v,
        "ema_20": f(ema_20),
        "rsi_14": f(rsi_14),
        "macd": f(macd_line),
        "macd_signal": f(macd_signal),
        "macd_hist": f(macd_hist),
        "bb_upper": f(bb_upper),
        "bb_middle": f(bb_middle),
        "bb_lower": f(bb_lower),
        "atr_14": f(atr_14),
        "adx_14": f(adx_14),
        "trend": {
            "above_sma_50": price is not None and sma_50_v is not None and price > sma_50_v,
            "above_sma_200": price is not None and sma_200_v is not None and price > sma_200_v,
            "golden_cross": (
                sma_50_v is not None and sma_200_v is not None and sma_50_v > sma_200_v
            ),
        },
    }
    cache.put("compute_indicators", params, out)
    return out


@function_tool
def compute_indicators(ticker: str, lookback_days: int = 365) -> dict:
    """Compute key technical indicators for a ticker on EOD data from Tiingo.

    Returns the latest reading + recent trend for: SMA(20/50/200), EMA(20),
    RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14), ADX(14). Also
    includes price relative to each MA so you can quickly see trend structure
    (golden/death cross, RSI extremes, MACD crossovers).

    Args:
        ticker: Symbol, e.g. "AAPL".
        lookback_days: History window. Default 365 — enough for SMA200 to be valid.
    """
    return _compute_indicators(ticker, lookback_days)


@function_tool
def screen_universe(
    tickers: list[str],
    rsi_max: float | None = None,
    rsi_min: float | None = None,
    above_sma_200: bool | None = None,
    above_sma_50: bool | None = None,
) -> dict:
    """Screen a list of tickers by technical criteria. Returns matches with
    their indicators.

    All filters are optional. The ticker list is your universe — for a sector
    scan, first call get_sector_holdings (or the screener) to get names, then
    pass them here.

    Args:
        tickers: Symbols to screen.
        rsi_max: Only include if RSI(14) <= this. e.g. 30 for oversold.
        rsi_min: Only include if RSI(14) >= this. e.g. 70 for overbought.
        above_sma_200: If True, only names trading above their 200d SMA.
        above_sma_50: If True, only names trading above their 50d SMA.
    """
    matches: list[dict] = []
    skipped: list[dict] = []
    for t in tickers:
        try:
            ind = _compute_indicators(t, lookback_days=365)
        except Exception as e:
            skipped.append({"ticker": t, "error": str(e)})
            continue
        if "error" in ind:
            skipped.append({"ticker": t, "error": ind["error"]})
            continue
        rsi = ind.get("rsi_14")
        if rsi_max is not None and (rsi is None or rsi > rsi_max):
            continue
        if rsi_min is not None and (rsi is None or rsi < rsi_min):
            continue
        if above_sma_200 is True and not ind["trend"]["above_sma_200"]:
            continue
        if above_sma_50 is True and not ind["trend"]["above_sma_50"]:
            continue
        matches.append({
            "ticker": t,
            "price": ind.get("price"),
            "rsi_14": rsi,
            "above_sma_50": ind["trend"]["above_sma_50"],
            "above_sma_200": ind["trend"]["above_sma_200"],
        })
    return {
        "criteria": {
            "rsi_max": rsi_max,
            "rsi_min": rsi_min,
            "above_sma_200": above_sma_200,
            "above_sma_50": above_sma_50,
        },
        "n_screened": len(tickers),
        "n_matches": len(matches),
        "matches": matches,
        "skipped": skipped[:10],
    }
