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
    "PANW": "0001327567",  # resolved via ingest_ticker.py -> SEC company_tickers.json, 2026-07-25
    "DELL": "0001571996",  # resolved via ingest_ticker.py -> SEC company_tickers.json, 2026-07-25
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
    # Real bug, found against PANW (2026-07-25): this used to return the
    # first tag with ANY entries, not the first tag with USABLE quarterly
    # entries. Companies tag revenue inconsistently -- PANW's "Revenues"
    # tag had data, but none of it was quarter-shaped (likely annual/legacy
    # entries only), so quarterly_series() downstream silently produced 0
    # quarters and the loop never tried the next candidate tag, even though
    # RevenueFromContractWithCustomerExcludingAssessedTax exists in
    # REVENUE_TAGS specifically for this situation. Now checks that
    # quarterly_series(entries) actually yields something before committing
    # to a tag. Backward-compatible: for tickers where the first tag already
    # had usable quarterly data (confirmed: ALAB, DELL), this changes
    # nothing.
    for tag in REVENUE_TAGS:
        entries = fetch_concept(cik, tag)
        if entries and quarterly_series(entries):
            return tag, entries
    raise ValueError(f"No revenue concept with usable quarterly data found for CIK {cik} -- tried {REVENUE_TAGS}")


def derive_missing_q4(quarterly_by_end: dict[str, dict], entries: list[dict]) -> list[dict]:
    """Companies routinely don't file a discrete 'three months ended
    Dec 31' duration fact for Q4 -- the 10-K reports the full fiscal
    year instead, so a pure quarterly-duration filter (as above) silently
    skips Q4 every year. That gap showed up directly in the first real
    run against ALAB (2025-12-31 missing from the printed quarters).

    Derive Q4 = FY total - (Q1 + Q2 + Q3) for any fiscal year where we
    have all three quarters plus the annual total, so deceleration/
    compression streaks are computed against 4 real quarters, not 3 with
    a silent gap.

    Matches quarters to a fiscal year by calendar containment (start/end
    falling inside the annual period), NOT by trusting XBRL's own `fy`
    field -- confirmed unreliable against real data. A later 10-Q's
    comparative prior-year column re-reports an earlier quarter under
    THAT LATER FILING's own fy tag (e.g. MRVL's Q1 FY26 figures reappear
    tagged fy=2027 inside the Q1 FY27 10-Q, filed as a comparison
    column). quarterly_series()'s "most recently filed wins" dedup then
    keeps that mis-tagged duplicate, which silently dropped Q1 out of
    FY2026's fy-keyed bucket -- covering found only 2 of 3 needed
    quarters and Q4 FY2026 was never derived, even though the real data
    was all present. Real values are identical between duplicates (only
    the fy tag differs), so containment-based matching is safe and
    correct here."""
    annual = []
    for e in entries:
        try:
            start = date.fromisoformat(e["start"])
            end = date.fromisoformat(e["end"])
        except (KeyError, ValueError):
            continue
        if 350 <= (end - start).days <= 380:
            annual.append(e)

    derived = []
    for fy_entry in annual:
        fy = fy_entry.get("fy")
        fy_start, fy_end = fy_entry["start"], fy_entry["end"]
        covering = [
            q for q in quarterly_by_end.values()
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
    """Status is decided by sequential QoQ growth direction over the
    last 3 quarters -- asymmetric on purpose: all 3 up is required for
    intact (hard to earn), 2-of-3 down is enough for at_risk (easy to
    trip), everything else is monitor. Replaces the previous
    YoY-deceleration-streak logic, which could call something "intact"
    purely because a very high YoY rate was decelerating gently, without
    ever checking whether revenue was actually still growing quarter to
    quarter. yoy_growth_by_quarter is still computed and returned
    unchanged -- the existing frontend chart reads it, and it remains
    useful independent context -- it just no longer drives status.

    Confirmed against real ALAB data (2026-07-26): QoQ deltas of
    +20.1%/+17.4%/+14.0% across the last 3 quarters -> intact, matching
    the prior logic's result for this ticker. Margin's equivalent
    redesign (classify_margin_trend, below) did NOT match its prior
    result on real data -- see that function's docstring.
    """
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

    # Same adjacency guard used throughout this file -- a gap between
    # quarters (missing filing, restatement) must not get silently
    # treated as "the next quarter" when computing a QoQ delta.
    def _is_adjacent_quarter(a: dict, b: dict) -> bool:
        return 75 <= (date.fromisoformat(b["end"]) - date.fromisoformat(a["end"])).days <= 100

    if len(series) < 4:
        return {"status": "insufficient_data", "quarters_available": len(series),
                "note": "Need >= 4 quarters to compute 3 QoQ deltas.",
                "yoy_growth_by_quarter": recent}

    last4 = series[-4:]
    qoq_growth = []
    for i in range(1, len(last4)):
        if not _is_adjacent_quarter(last4[i - 1], last4[i]):
            qoq_growth = []  # a gap breaks the streak -- don't compute across it
            break
        qoq_growth.append({
            "period": last4[i]["end"],
            "qoq_pct": round((last4[i]["val"] - last4[i - 1]["val"]) / last4[i - 1]["val"] * 100, 1),
        })

    if len(qoq_growth) < 3:
        return {"status": "insufficient_data", "qoq_quarters_available": len(qoq_growth),
                "note": "Need 3 consecutive, gap-free quarters to compute the QoQ streak.",
                "yoy_growth_by_quarter": recent}

    down = sum(1 for q in qoq_growth if q["qoq_pct"] < 0)
    up_all = all(q["qoq_pct"] > 0 for q in qoq_growth)

    if down >= 2:
        status = "at_risk"
    elif up_all:
        status = "intact"
    else:
        status = "monitor"

    # Chart data for the frontend (2026-07-27, Maiu: revenue chart should
    # show QoQ growth, not YoY, and cover ~2 years) -- a SEPARATE
    # computation from qoq_growth above, on purpose: qoq_growth is
    # deliberately frozen at the last 3 deltas because that's the exact
    # window the intact/monitor/at_risk status streak is tuned against
    # (see this function's own docstring). Widening qoq_growth itself to
    # show more history would silently widen the status window too. This
    # walks the full series instead, capped at the last 8 quarters (~2
    # years) of QoQ deltas, purely for display -- status above never reads
    # this variable.
    qoq_growth_chart = []
    for i in range(1, len(series)):
        if not _is_adjacent_quarter(series[i - 1], series[i]):
            continue  # skip just this one broken pair -- a display list, not a decision streak
        qoq_growth_chart.append({
            "period": series[i]["end"],
            "qoq_pct": round((series[i]["val"] - series[i - 1]["val"]) / series[i - 1]["val"] * 100, 1),
        })
    qoq_growth_chart = qoq_growth_chart[-8:]

    return {
        "status": status,
        "yoy_growth_by_quarter": recent,
        "qoq_growth_by_quarter": qoq_growth,
        "qoq_growth_chart": qoq_growth_chart,
    }


def classify_margin_trend(revenue_series: list[dict], gross_profit_series: list[dict]) -> dict:
    """Status logic, redesigned alongside classify_revenue_trend
    (2026-07-26): a severity override first (any single QoQ compression
    over 400bps forces at_risk immediately, regardless of streak --
    carried over from this function's own prior 400bps single-quarter
    threshold, not a newly invented number), then the same asymmetric
    QoQ-streak shape as revenue -- 2-of-3 quarters compressing is enough
    for at_risk, all 3 expanding is required for intact, else monitor.
    Margin keeps a severity override (revenue deliberately does not):
    the real motivating case here is a single-quarter guidance shock
    (a one-time customer agreement cutting ALAB's Q2 guide ~300bps),
    which a pure 3-quarter streak would miss for two more quarters.

    Confirmed against real ALAB data (2026-07-26): QoQ deltas of
    +41bps/-68bps/+69bps across the last 3 quarters -> monitor (1 of 3
    compressing, no severity trip) -- this is a REAL CHANGE from the
    prior peak-relative logic's result, which read intact for the same
    data (0 consecutive compression quarters, since the most recent
    quarter expanded). Both are defensible reads of the same numbers;
    monitor was chosen deliberately as more accurate given the
    mixed/oscillating pattern, not because the prior result was wrong.
    """
    rev_by_end = {e["end"]: e["val"] for e in revenue_series}
    margins = []
    for e in gross_profit_series:
        if e["end"] in rev_by_end and rev_by_end[e["end"]]:
            margins.append({"period": e["end"], "margin_pct": round(e["val"] / rev_by_end[e["end"]] * 100, 2)})
    margins.sort(key=lambda m: m["period"])

    if len(margins) < 4:
        return {"status": "insufficient_data", "quarters_available": len(margins),
                "note": "Need >= 4 quarters to compute 3 QoQ deltas."}

    # Two SEPARATE windows over the same margins list (2026-07-27, Maiu):
    # status_window is exactly what this function always used (last 4
    # quarters -> 3 QoQ deltas) and drives the intact/monitor/at_risk call
    # below, untouched. chart_window is only for what the frontend
    # displays -- the raw margin % per quarter (a level, not a growth
    # rate; margin has never been charted as a rate of change and stays
    # that way here), widened to the last 8 quarters (~2 years) of
    # whatever's available. Before this split, one list (`recent`) did
    # both jobs, so widening the chart would have silently widened the
    # status calculation too -- see chat discussion, 2026-07-27.
    status_window = margins[-4:]
    chart_window = margins[-8:]

    # Same adjacency guard as classify_revenue_trend -- margins here come
    # from matching revenue_series to gross_profit_series by exact end
    # date, so a gap in either underlying series (even after the Q4 fix)
    # would otherwise get silently treated as "the next quarter."
    def _is_adjacent_quarter(a: dict, b: dict) -> bool:
        return 75 <= (date.fromisoformat(b["period"]) - date.fromisoformat(a["period"])).days <= 100

    qoq_deltas = []
    for i in range(1, len(status_window)):
        if not _is_adjacent_quarter(status_window[i - 1], status_window[i]):
            qoq_deltas = []  # a gap breaks the streak -- don't compute across it
            break
        qoq_deltas.append({
            "period": status_window[i]["period"],
            "qoq_bps": round((status_window[i]["margin_pct"] - status_window[i - 1]["margin_pct"]) * 100),
        })

    if len(qoq_deltas) < 3:
        return {"status": "insufficient_data", "qoq_quarters_available": len(qoq_deltas),
                "note": "Need 3 consecutive, gap-free quarters to compute the QoQ streak.",
                "margin_by_quarter": chart_window}

    worst_single_drop_bps = min((d["qoq_bps"] for d in qoq_deltas), default=0)
    down = sum(1 for d in qoq_deltas if d["qoq_bps"] < 0)
    up_all = all(d["qoq_bps"] > 0 for d in qoq_deltas)

    if worst_single_drop_bps < -400:
        status = "at_risk"
    elif down >= 2:
        status = "at_risk"
    elif up_all:
        status = "intact"
    else:
        status = "monitor"

    return {
        "status": status,
        "margin_by_quarter": chart_window,
        "qoq_bps_by_quarter": qoq_deltas,
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
