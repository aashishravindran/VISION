"""Fundamentals + earnings tools backed by FMP.

FMP free covers ALL S&P 500 names (not just Dow 30 like Tiingo), and the
earnings calendar exposes forward dates that Tiingo paywalled.
"""
from datetime import date, timedelta

from agents import function_tool

from vision.data import fmp


def _slim_income(row: dict) -> dict:
    return {
        "period": row.get("period"),
        "year": row.get("calendarYear"),
        "date": row.get("date"),
        "revenue": row.get("revenue"),
        "gross_profit": row.get("grossProfit"),
        "operating_income": row.get("operatingIncome"),
        "net_income": row.get("netIncome"),
        "eps": row.get("eps"),
        "eps_diluted": row.get("epsdiluted"),
        "ebit": row.get("ebit") or row.get("operatingIncome"),
        "ebitda": row.get("ebitda"),
        "weighted_avg_shares": row.get("weightedAverageShsOut"),
    }


def _slim_balance(row: dict) -> dict:
    return {
        "period": row.get("period"),
        "year": row.get("calendarYear"),
        "date": row.get("date"),
        "total_assets": row.get("totalAssets"),
        "total_liabilities": row.get("totalLiabilities"),
        "total_equity": row.get("totalStockholdersEquity"),
        "cash_and_equivalents": row.get("cashAndCashEquivalents"),
        "short_term_investments": row.get("shortTermInvestments"),
        "total_debt": row.get("totalDebt"),
        "long_term_debt": row.get("longTermDebt"),
        "current_assets": row.get("totalCurrentAssets"),
        "current_liabilities": row.get("totalCurrentLiabilities"),
        "inventory": row.get("inventory"),
    }


def _slim_cash(row: dict) -> dict:
    return {
        "period": row.get("period"),
        "year": row.get("calendarYear"),
        "date": row.get("date"),
        "operating_cash_flow": row.get("operatingCashFlow") or row.get("netCashProvidedByOperatingActivities"),
        "investing_cash_flow": row.get("netCashUsedForInvestingActivities"),
        "financing_cash_flow": row.get("netCashUsedProvidedByFinancingActivities"),
        "free_cash_flow": row.get("freeCashFlow"),
        "capex": row.get("capitalExpenditure"),
        "net_change_in_cash": row.get("netChangeInCash"),
    }


@function_tool
def get_fundamentals(ticker: str) -> dict:
    """Get fundamental financial data: income statement, balance sheet,
    cash flow. Returns the four most recent annual reporting periods plus
    the latest key metrics (P/E, P/B, ROE, debt-to-equity).

    Args:
        ticker: Stock symbol.
    """
    try:
        inc = fmp.income_statement(ticker, limit=4)
        bs = fmp.balance_sheet(ticker, limit=4)
        cf = fmp.cash_flow(ticker, limit=4)
        km = fmp.key_metrics(ticker, limit=1)
    except fmp.FMPNoKeyError:
        return {"ticker": ticker.upper(), "error": "no_key",
                "error_message": "FMP_API_KEY not set on the server."}
    except fmp.FMPTierGatedError:
        return {"ticker": ticker.upper(), "error": "tier_gated",
                "error_message": (
                    f"FMP free tier does not cover {ticker.upper()}. "
                    f"Try lookup_ticker_via_web for a web-sourced summary."
                )}
    except fmp.FMPRateLimitError as e:
        return {"ticker": ticker.upper(), "error": "rate_limited", "error_message": str(e)}
    except fmp.FMPError as e:
        return {"ticker": ticker.upper(), "error": "fmp_error", "error_message": str(e)}

    if not inc and not bs and not cf:
        return {"ticker": ticker.upper(), "error": "No fundamentals available for this ticker."}

    latest_metrics = (km or [{}])[0] if km else {}
    return {
        "ticker": ticker.upper(),
        "as_of": latest_metrics.get("date"),
        "key_metrics": {
            "market_cap": latest_metrics.get("marketCap"),
            "enterprise_value": latest_metrics.get("enterpriseValue"),
            "pe_ratio": latest_metrics.get("peRatio"),
            "pb_ratio": latest_metrics.get("pbRatio"),
            "roe": latest_metrics.get("roe"),
            "roic": latest_metrics.get("roic"),
            "current_ratio": latest_metrics.get("currentRatio"),
            "debt_to_equity": latest_metrics.get("debtToEquity"),
            "free_cash_flow_yield": latest_metrics.get("freeCashFlowYield"),
        },
        "income_statement": [_slim_income(r) for r in inc],
        "balance_sheet": [_slim_balance(r) for r in bs],
        "cash_flow": [_slim_cash(r) for r in cf],
    }


@function_tool
def get_earnings(ticker: str) -> dict:
    """Get earnings — both historical results and upcoming calendar dates.

    Past entries have epsActual and revenueActual; upcoming entries have
    epsEstimated and revenueEstimated only. Use this for "when does X
    report?" or "did X beat last quarter?"

    Args:
        ticker: Stock symbol.
    """
    try:
        # 6 months back, 6 months forward — wide window covers most uses
        from_date = (date.today() - timedelta(days=180)).isoformat()
        to_date = (date.today() + timedelta(days=180)).isoformat()
        rows = fmp.earnings_calendar(from_date=from_date, to_date=to_date, ticker=ticker)
    except fmp.FMPNoKeyError:
        return {"ticker": ticker.upper(), "error": "no_key",
                "error_message": "FMP_API_KEY not set on the server."}
    except fmp.FMPTierGatedError:
        return {"ticker": ticker.upper(), "error": "tier_gated",
                "error_message": (
                    f"FMP free tier does not cover {ticker.upper()}. "
                    f"Try lookup_ticker_via_web for a web-sourced summary."
                )}
    except fmp.FMPRateLimitError as e:
        return {"ticker": ticker.upper(), "error": "rate_limited", "error_message": str(e)}
    except fmp.FMPError as e:
        return {"ticker": ticker.upper(), "error": "fmp_error", "error_message": str(e)}

    if not rows:
        return {"ticker": ticker.upper(), "history": [], "upcoming": [], "note": "No earnings on file."}

    today = date.today().isoformat()
    history = [r for r in rows if (r.get("date") or "9999") <= today and r.get("epsActual") is not None]
    upcoming = [r for r in rows if (r.get("date") or "0000") > today]

    history.sort(key=lambda r: r.get("date") or "", reverse=True)
    upcoming.sort(key=lambda r: r.get("date") or "")

    return {
        "ticker": ticker.upper(),
        "history": [
            {
                "date": r.get("date"),
                "eps_actual": r.get("epsActual"),
                "eps_estimated": r.get("epsEstimated"),
                "eps_surprise_pct": (
                    round((r["epsActual"] - r["epsEstimated"]) / r["epsEstimated"] * 100, 2)
                    if r.get("epsActual") is not None and r.get("epsEstimated")
                    else None
                ),
                "revenue_actual": r.get("revenueActual"),
                "revenue_estimated": r.get("revenueEstimated"),
            }
            for r in history[:8]
        ],
        "upcoming": [
            {
                "date": r.get("date"),
                "eps_estimated": r.get("epsEstimated"),
                "revenue_estimated": r.get("revenueEstimated"),
            }
            for r in upcoming[:4]
        ],
    }
