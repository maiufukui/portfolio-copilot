"""
Fundamentals Health Score sub-signal: revenue growth trend + margin trend.

Structured, deterministic -- pulled from SEC EDGAR's XBRL company-concept
API, not LLM-parsed from transcript prose (Task 2 section 4 explicitly
calls this out: these two signals use exact tagged financial values, not
inference over earnings-call language).

Endpoint pattern (no API key required, just a real contact User-Agent per
SEC's fair-use policy -- requests without one get rejected):
    https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json

NOT YET LIVE-TESTED against real data -- this sandbox has no outbound
network access to data.sec.gov (confirmed: direct requests return
"blocked-by-allowlist"). Structure below follows SEC's documented XBRL
frames API shape. Run this locally and paste the output back -- same
pattern as every other test_q*.py script in this project.

Usage:
    python fetch_xbrl_financials.py --ticker ALAB
"""

from __future__ import annotations

import argparse
from datetime import date

import requests

# SEC requires a real identifying User-Agent (company/app name + contact
# email) on every request -- generic/missing User-Agents get 403'd.
HEADERS = {"User-Agent": "PersonalPortfolioCopilot maiu.fukui@gmail.com"}

# MVP shortcut: hardcoded ticker -> CIK for the 4 tickers this project
# tests against. Full lookup uses SEC's company_tickers.json
# (https://www.sec.gov/files/company_tickers.json), not wired in yet.
TICKER_TO_CIK = {
    "ALAB": "0001736297",
    "AAPL": "0000320193",
    "MRVL": "0001835632",
    "NBIS": "0001513845",  # confirmed via app.edgar.tools/companies/NBIS
}

# Revenue is tagged inconsistently across companies -- try in this order,
# use the first one that returns data.
REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]
GROSS_PROFIT_TAG = "GrossProfit"


def fetch_concept(cik: str, tag: str) -> list[dict] | None:
    """Fetch one XBRL concept's full reported history. Returns the raw
    list of {start, end, val, fy, fp, form, frame, ...} entries, or None
    if this tag isn't reported by this company (try the next one in
    REVENUE_TAGS)."""
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("units", {}).get("USD", [])


def fetch_revenue(cik: str) -> tuple[str, list[dict]]:
    for tag in REVENUE_TAGS:
        entries = fetch_concept(cik, tag)
        if entries:
            return tag, entries
    raise ValueError(f"No revenue concept found for CIK {cik} -- tried {REVENUE_TAGS}")


def derive_missing_q4(quarterly_by_end: dict[str, dict], entries: list[dict]) -> list[dict]:
    """Companies routinely don't file a discrete 'three months ended
    Dec 31' duration fact for Q4 -- the 10-K reports the full fiscal
    year instead, so a pure quarterly-duration filter (as above) silently
    skips Q4 every year. That gap showed up directly in the first real
    run against ALAB (2025-12-31 missing from the printed quarters).

    Derive Q4 = FY total - (Q1 + Q2 + Q3) for any fiscal year where we
    have all three quarters plus the annual total, so deceleration/
    compression streaks are computed against 4 real quarters, not 3 with
    a silent gap."""
    annual = []
    for e in entries:
        try:
            start = date.fromisoformat(e["start"])
            end = date.fromisoformat(e["end"])
        except (KeyError, ValueError):
            continue
        if 350 <= (end - start).days <= 380:
            annual.append(e)

    quarterly_by_fy: dict[int, list[dict]] = {}
    for e in quarterly_by_end.values():
        fy = e.get("fy")
        if fy is not None:
            quarterly_by_fy.setdefault(fy, []).append(e)

    derived = []
    for fy_entry in annual:
        fy = fy_entry.get("fy")
        fy_start, fy_end = fy_entry["start"], fy_entry["end"]
        covering = [
            q for q in quarterly_by_fy.get(fy, [])
            if q["start"] >= fy_start and q["end"] <= fy_end
        ]
        if len(covering) != 3:
            continue  # need exactly 3 known quarters to solve for the 4th
        covering.sort(key=lambda q: q["end"])
        if covering[-1]["end"] >= fy_end:
            continue  # no gap -- three quarters already reach year-end
        q4_val = fy_entry["val"] - sum(q["val"] for q in covering)
        derived.append({
            "start": covering[-1]["end"],
            "end": fy_end,
            "val": q4_val,
            "fy": fy,
            "fp": "Q4",
            "form": "10-K (derived: FY - Q1 - Q2 - Q3)",
            "filed": fy_entry.get("filed", ""),
        })
    return derived


def quarterly_series(entries: list[dict]) -> list[dict]:
    """XBRL company-concept data mixes quarterly (10-Q), annual (10-K),
    and cumulative-year-to-date entries for the same tag. Keep only
    entries that look like a single ~90-day period (quarterly), dedup by
    end date (a filing can restate the same period more than once --
    keep the most recently filed value), derive any missing Q4s, and
    sort chronologically."""
    quarterly = []
    for e in entries:
        try:
            start = date.fromisoformat(e["start"])
            end = date.fromisoformat(e["end"])
        except (KeyError, ValueError):
            continue
        days = (end - start).days
        if 80 <= days <= 100:  # roughly one quarter
            quarterly.append(e)

    by_end: dict[str, dict] = {}
    for e in quarterly:
        key = e["end"]
        if key not in by_end or e.get("filed", "") > by_end[key].get("filed", ""):
            by_end[key] = e

    for derived_q4 in derive_missing_q4(by_end, entries):
        key = derived_q4["end"]
        if key not in by_end:  # never overwrite a real reported quarter
            by_end[key] = derived_q4

    return sorted(by_end.values(), key=lambda e: e["end"])


def find_year_ago_quarter(series: list[dict], idx: int, tolerance_days: int = 35) -> dict | None:
    """Find the entry whose period-end is closest to exactly 1 year (365
    days) before series[idx]'s period-end, within tolerance.

    This replaces a fixed 'series[i-4]' index lookback, which silently
    assumes zero gaps anywhere in the series -- confirmed broken in
    practice: after fixing the Q4-derivation gap, the same historical
    quarters' YoY figures changed (e.g. 2025-03-31 went from 331.8% to
    144.3%) purely because the array shifted, not because the underlying
    revenue facts did. Matching by actual calendar distance instead of
    array position is correct regardless of how many entries the series
    has or where any remaining gaps are."""
    target_end = date.fromisoformat(series[idx]["end"])
    target_prior = target_end.replace(year=target_end.year - 1)

    best, best_diff = None, None
    for e in series:
        if e is series[idx]:
            continue
        e_end = date.fromisoformat(e["end"])
        diff = abs((e_end - target_prior).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best, best_diff = e, diff
    return best


def classify_revenue_trend(series: list[dict]) -> dict:
    """Task 2 section 4 thresholds. Needs at least 5 quarters (current +
    a real prior-year comp for at least 2 of the last 4) to compute a
    meaningful trend."""
    if len(series) < 5:
        return {"status": "insufficient_data", "quarters_available": len(series)}

    yoy_growth = []
    for i in range(len(series)):
        year_ago_entry = find_year_ago_quarter(series, i)
        if year_ago_entry and year_ago_entry["val"]:
            yoy_growth.append({
                "period": series[i]["end"],
                "yoy_pct": round((series[i]["val"] - year_ago_entry["val"]) / year_ago_entry["val"] * 100, 1),
                "compared_to": year_ago_entry["end"],
            })

    if len(yoy_growth) < 2:
        return {"status": "insufficient_data", "yoy_quarters_available": len(yoy_growth),
                "note": "No quarter had a real prior-year comp within 35 days -- can't compute YoY trend yet."}

    recent = yoy_growth[-4:]
    decel_streak = 0
    for i in range(len(recent) - 1, 0, -1):
        if recent[i]["yoy_pct"] < recent[i - 1]["yoy_pct"]:
            decel_streak += 1
        else:
            break

    latest = recent[-1]["yoy_pct"]
    latest_drop = recent[-1]["yoy_pct"] - recent[-2]["yoy_pct"] if len(recent) >= 2 else 0

    # Only treat the last 3 series entries as a valid consecutive-QoQ-decline
    # check if they're actually ~90 days apart -- guards against the same
    # class of gap bug the YoY fix above addresses, for any gap this
    # script's Q4 derivation doesn't happen to catch.
    def _is_adjacent_quarter(a: dict, b: dict) -> bool:
        return 75 <= (date.fromisoformat(b["end"]) - date.fromisoformat(a["end"])).days <= 100

    qoq_declined_2 = (
        len(series) >= 3
        and _is_adjacent_quarter(series[-2], series[-1])
        and _is_adjacent_quarter(series[-3], series[-2])
        and series[-1]["val"] < series[-2]["val"] < series[-3]["val"]
    )

    if decel_streak >= 3 or latest < 0 or qoq_declined_2:
        status = "at_risk"
    elif decel_streak >= 2 or latest_drop < -15:
        status = "monitor"
    else:
        status = "intact"

    return {"status": status, "yoy_growth_by_quarter": recent, "consecutive_deceleration_quarters": decel_streak}


def classify_margin_trend(revenue_series: list[dict], gross_profit_series: list[dict]) -> dict:
    rev_by_end = {e["end"]: e["val"] for e in revenue_series}
    margins = []
    for e in gross_profit_series:
        if e["end"] in rev_by_end and rev_by_end[e["end"]]:
            margins.append({"period": e["end"], "margin_pct": round(e["val"] / rev_by_end[e["end"]] * 100, 2)})
    margins.sort(key=lambda m: m["period"])

    if len(margins) < 3:
        return {"status": "insufficient_data", "quarters_available": len(margins)}

    recent = margins[-4:]
    peak = max(m["margin_pct"] for m in recent)
    latest = recent[-1]["margin_pct"]
    cumulative_compression_bps = round((peak - latest) * 100)
    single_quarter_drop_bps = round((recent[-2]["margin_pct"] - recent[-1]["margin_pct"]) * 100) if len(recent) >= 2 else 0

    # Same adjacency guard as classify_revenue_trend -- margins here come
    # from matching revenue_series to gross_profit_series by exact end
    # date, so a gap in either underlying series (even after the Q4 fix)
    # would otherwise get silently treated as "the next quarter."
    def _is_adjacent_quarter(a: dict, b: dict) -> bool:
        return 75 <= (date.fromisoformat(b["period"]) - date.fromisoformat(a["period"])).days <= 100

    compressed_streak = 0
    for i in range(len(recent) - 1, 0, -1):
        if not _is_adjacent_quarter(recent[i - 1], recent[i]):
            break  # gap between these two -- don't count across it
        if recent[i]["margin_pct"] < recent[i - 1]["margin_pct"]:
            compressed_streak += 1
        else:
            break

    if compressed_streak >= 3 or cumulative_compression_bps > 500 or single_quarter_drop_bps > 400:
        status = "at_risk"
    elif compressed_streak >= 2 or single_quarter_drop_bps > 200:
        status = "monitor"
    else:
        status = "intact"

    return {
        "status": status,
        "margin_by_quarter": recent,
        "consecutive_compression_quarters": compressed_streak,
        "cumulative_compression_bps_from_peak": cumulative_compression_bps,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()

    ticker = args.ticker.upper()
    if ticker not in TICKER_TO_CIK:
        raise SystemExit(f"No CIK mapped for {ticker}. Add it to TICKER_TO_CIK.")
    cik = TICKER_TO_CIK[ticker]

    print(f"Fetching XBRL revenue history for {ticker} (CIK {cik})...")
    tag_used, revenue_raw = fetch_revenue(cik)
    print(f"  Using tag: {tag_used}")
    revenue_q = quarterly_series(revenue_raw)
    print(f"  {len(revenue_q)} quarterly periods found.")

    revenue_result = classify_revenue_trend(revenue_q)
    print("\n=== Revenue Growth Trend ===")
    print(revenue_result)

    print(f"\nFetching XBRL gross profit history for {ticker}...")
    gross_profit_raw = fetch_concept(cik, GROSS_PROFIT_TAG)
    if not gross_profit_raw:
        print(f"  No '{GROSS_PROFIT_TAG}' tag reported -- margin signal unavailable for this company.")
    else:
        gross_profit_q = quarterly_series(gross_profit_raw)
        margin_result = classify_margin_trend(revenue_q, gross_profit_q)
        print("\n=== Margin Trend ===")
        print(margin_result)


if __name__ == "__main__":
    main()
