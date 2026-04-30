"""Fundamentals + earnings tools backed by Tiingo.

Tiingo's free tier exposes daily fundamentals (market cap, P/E, P/B) and the
full statement series (income, balance sheet, cash flow). Earnings *calendar*
(future dates, EPS estimates) is on a paid Tiingo tier — for now we surface
historical earnings via the income statement and flag the limitation honestly.
"""
from agents import function_tool

from vision.data import tiingo


# Tiingo statement dataCodes we care about (out of ~150). These map to the
# typical income / balance / cash flow line items that drive analysis.
INCOME_KEYS = {
    "revenue": "revenue",
    "grossProfit": "gross_profit",
    "operatingIncome": "operating_income",
    "netinc": "net_income",
    "eps": "eps",
    "epsDil": "eps_diluted",
    "ebit": "ebit",
    "ebitda": "ebitda",
    "shareswa": "weighted_avg_shares",
}

BALANCE_KEYS = {
    "totalAssets": "total_assets",
    "totalLiabilities": "total_liabilities",
    "totalEquity": "total_equity",
    "cashAndEq": "cash_and_equivalents",
    "debt": "total_debt",
    "longTermDebt": "long_term_debt",
    "currentAssets": "current_assets",
    "currentLiabilities": "current_liabilities",
    "inventory": "inventory",
    "investmentsCurrent": "short_term_investments",
}

CASH_FLOW_KEYS = {
    "freeCashFlow": "free_cash_flow",
    "ncfo": "operating_cash_flow",
    "ncfi": "investing_cash_flow",
    "ncff": "financing_cash_flow",
    "capex": "capex",
    "ncf": "net_change_in_cash",
}


def _pluck(items: list[dict], key_map: dict[str, str]) -> dict:
    """Convert Tiingo's [{dataCode, value}, ...] into a flat dict of friendly keys."""
    by_code = {item.get("dataCode"): item.get("value") for item in items or []}
    return {friendly: by_code.get(code) for code, friendly in key_map.items()}


@function_tool
def get_fundamentals(ticker: str) -> dict:
    """Get fundamental financial data: income statement, balance sheet,
    cash flow. Returns the four most recent annual reporting periods.

    Use for a quick fundamentals snapshot — revenue trend, earnings, debt
    levels, cash position, free cash flow.

    Args:
        ticker: Stock symbol, e.g. "AAPL".
    """
    s = tiingo.get_statements(ticker)
    if not s:
        return {
            "ticker": ticker.upper(),
            "error": "No statements available from Tiingo (may require paid tier for this ticker).",
        }
    if not s.get("annual_periods"):
        return {
            "ticker": ticker.upper(),
            "n_periods_available": s.get("n_periods_available", 0),
            "periods": [],
            "note": "No annual periods returned. The ticker may only have quarterly data on the free tier.",
        }

    periods = []
    for p in s["annual_periods"]:
        sd = p.get("statementData") or {}
        periods.append({
            "year": p.get("year"),
            "quarter": p.get("quarter"),
            "date": p.get("date"),
            "income_statement": _pluck(sd.get("incomeStatement"), INCOME_KEYS),
            "balance_sheet": _pluck(sd.get("balanceSheet"), BALANCE_KEYS),
            "cash_flow": _pluck(sd.get("cashFlow"), CASH_FLOW_KEYS),
        })

    return {
        "ticker": ticker.upper(),
        "n_periods_available": s.get("n_periods_available"),
        "periods": periods,
    }


@function_tool
def get_earnings(ticker: str) -> dict:
    """Get historical earnings (EPS) for the past four annual periods derived
    from the income statement. Tiingo's forward earnings calendar requires a
    paid tier; this tool surfaces what's available on the free plan.

    Args:
        ticker: Stock symbol.
    """
    s = tiingo.get_statements(ticker)
    if not s or not s.get("annual_periods"):
        return {
            "ticker": ticker.upper(),
            "error": "No earnings data available from Tiingo on the free tier for this ticker.",
        }
    history = []
    for p in s["annual_periods"]:
        income = _pluck((p.get("statementData") or {}).get("incomeStatement"), INCOME_KEYS)
        history.append({
            "year": p.get("year"),
            "date": p.get("date"),
            "eps": income.get("eps"),
            "eps_diluted": income.get("eps_diluted"),
            "net_income": income.get("net_income"),
            "revenue": income.get("revenue"),
        })
    return {
        "ticker": ticker.upper(),
        "annual_history": history,
        "note": "Forward earnings dates and analyst estimates require a Tiingo paid tier.",
    }
