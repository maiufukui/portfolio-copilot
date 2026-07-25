"""
Earnings-call transcript downloader — Portfolio Tracker Assistant (item 3)

Automates what was previously a one-time manual process: locate each
ticker's most recent Motley Fool earnings-call transcript, fetch it,
extract the real structured content, and save clean .txt into
Data/<TICKER>/ — matching the exact format the existing 4 tickers'
hand-built transcripts already use, so parent_child_retriever.py's
split_transcript_into_turns() needs zero changes to consume this
script's output.

Verified directly against this project's own saved copy of a real
fool.com page (Data/ALAB/Astera Labs (ALAB) Q1 2026 Earnings Transcript
_ The Motley Fool.html) before writing any of this — not assumed from
general scraping knowledge. Two concrete, load-bearing findings from
that inspection:

1. fool.com is a Next.js app. The actual article body (headings,
   participant list, transcript paragraphs) is NOT literal <h2>/<p> tags
   in the raw HTML document — it's server-rendered as HTML strings
   embedded inside JSON-escaped React Server Component ("flight")
   payloads: self.__next_f.push([1, "..."]) calls scattered through the
   page. extract_article_html() below decodes and reassembles these
   into one normal HTML blob BeautifulSoup can parse. Every structural
   element this script needs carries a real id attribute:
   id="date", id="call-participants", id="takeaways", id="summary",
   id="industry-glossary", id="full-conference-call-transcript" —
   confirmed present verbatim in the real saved page, not guessed.
2. Multi-paragraph speaker turns only carry the speaker's name (as a
   leading <strong>Name:</strong>) on their FIRST paragraph; subsequent
   paragraphs of the same turn are plain, unmarked <p> siblings until
   the next <strong>Name:</strong> appears. extract_transcript_turns()
   below reconstructs turns on that basis.

NOT verified from this dev sandbox: fetching a live fool.com page
end-to-end, and Tavily actually finding the right URL for a ticker this
script has never seen (PANW/DELL). This sandbox's outbound network is
allowlisted and has confirmed-blocked several other domains already
(Yahoo Finance, Render Postgres, OpenAI) — same likely story here.
Verify for real before trusting this on a new ticker:

    python fetch_transcripts.py --ticker PANW

A saved Data/PANW/transcript_*.txt that passes qa_gate() (printed to
stdout either way) confirms it end-to-end. If it fails, the ticker is
flagged and skipped, not silently written -- see qa_gate().

Usage:
    pip install -r requirements.txt   # requests, beautifulsoup4, python-dotenv (already present)
    python fetch_transcripts.py                  # all tickers in TICKERS
    python fetch_transcripts.py --ticker PANW     # just one
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_q2 import search_tavily  # noqa: E402 -- reuses the already-integrated Tavily wrapper

load_dotenv()

# Mirrors fetch_edgar_filings.py's own TICKERS list -- same "one ticker
# list per ingestion script" debt already flagged elsewhere in this
# project (XBRL's CIK dict, backfill_price_history.py), not a new
# decision. PANW/DELL included now since this script's whole purpose is
# to make them real (item 6) -- update all these lists together going
# forward, not just this one.
TICKERS = ["ALAB", "AAPL", "MRVL", "NBIS", "PANW", "DELL"]

# Only used to build the Tavily search query -- not a source-of-truth
# company-name mapping (that's app/tools.py's TICKER_TO_COMPANY).
# Deliberately not importing app.tools here, same reasoning as
# backfill_price_history.py: avoid dragging the full LangChain/Qdrant
# import chain into a one-time ingestion script that has nothing to do
# with any of that.
TICKER_TO_COMPANY = {
    "ALAB": "Astera Labs",
    "AAPL": "Apple",
    "MRVL": "Marvell",
    "NBIS": "Nebius",
    "PANW": "Palo Alto Networks",
    "DELL": "Dell Technologies",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DATA_DIR = "Data"


def find_transcript_url(ticker: str, company: str, api_key: str) -> str | None:
    """Tavily general-web search for this ticker's most recent Motley Fool
    earnings-call transcript page. Motley Fool has no predictable URL
    pattern per ticker/quarter -- confirmed: ALAB's real URL is
    fool.com/earnings/call-transcripts/2026/05/05/astera-labs-alab-q1-2026-earnings-transcript/,
    which cannot be constructed from ticker + quarter alone (it embeds
    the filing date and a slugified company name). time_range="year",
    not "week"/"month", since a quarter's actual reporting date relative
    to whenever this script runs is unpredictable -- narrower would risk
    missing the transcript entirely for a ticker that reported 2 months
    ago.
    """
    query = f"{company} {ticker} earnings call transcript site:fool.com"
    results = search_tavily(query, api_key, time_range="year", max_results=10, topic="general")
    candidates = [
        r["url"]
        for r in results
        if "fool.com" in r.get("url", "") and "call-transcripts" in r.get("url", "")
    ]
    return candidates[0] if candidates else None


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return resp.text


_FLIGHT_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def extract_article_html(raw_html: str) -> str:
    """Decode every Next.js flight-payload chunk's JSON string content
    (unescaping \\u003c etc. back into real < >) and concatenate them,
    in document order, into one HTML blob. See module docstring finding
    #1 for why this is necessary -- the article body isn't literal HTML
    in the raw response.
    """
    parts = []
    for chunk in _FLIGHT_CHUNK_RE.findall(raw_html):
        try:
            parts.append(json.loads('"' + chunk + '"'))
        except json.JSONDecodeError:
            continue  # a handful of non-string/malformed chunks are expected and harmless
    return "\n".join(parts)


def _section_bullets(soup: BeautifulSoup, section_id: str) -> list[str]:
    h2 = soup.find(id=section_id)
    if not h2:
        return []
    ul = h2.find_next_sibling("ul")
    if not ul:
        return []
    return [li.get_text(" ", strip=True) for li in ul.find_all("li", recursive=False)]


def _section_text(soup: BeautifulSoup, section_id: str) -> str:
    h2 = soup.find(id=section_id)
    if not h2:
        return ""
    p = h2.find_next_sibling("p")
    return p.get_text(" ", strip=True) if p else ""


_PAGE_CHROME_MARKERS = ('["$",', '"className":', '"articleKeys":', '"pageData":')


def _looks_like_page_chrome(text: str) -> bool:
    """True if `text` is leaked React-element JSON, not real article
    content. Real finding, verified against this project's own saved
    ALAB page: not every flight-payload chunk is pre-rendered HTML --
    unrelated page regions (related-articles widgets, disclosure-policy
    metadata, promo cards) are serialized as raw React element JSON
    (["$", "p", key, {"children": ...}]) instead, and after
    concatenating every chunk into one blob, those show up as
    BeautifulSoup siblings right after the real transcript's closing
    paragraph. Confirmed on the real page: the boundary sits right after
    the standard Motley Fool disclosure footer ("The Motley Fool
    recommends X. The Motley Fool has a disclosure policy.") -- i.e.
    after this triggers, the real transcript is already fully captured,
    nothing legitimate is being cut short.
    """
    return any(marker in text for marker in _PAGE_CHROME_MARKERS)


def extract_transcript_turns(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Every (speaker, text) turn under the full-conference-call-transcript
    heading. See module docstring finding #2 -- only a turn's first
    paragraph carries the <strong>Name:</strong> marker; later
    paragraphs of the same turn are unmarked continuations. Paragraphs
    before the FIRST speaker marker (the IR housekeeping/legal-
    disclaimer preamble every call opens with) are bucketed under an
    explicit "(unattributed intro)" label rather than guessed at --
    parent_child_retriever.py already treats a large preamble block
    before the first real speaker turn as a known, handled case (its
    own comment notes Data/ALAB's preamble was 34% of that file), so
    this doesn't need to be a perfect attribution, just an honest one.
    """
    h2 = soup.find(id="full-conference-call-transcript")
    if not h2:
        return []

    turns: list[tuple[str, str]] = []
    current_speaker = "(unattributed intro)"
    current_paras: list[str] = []

    node = h2.find_next_sibling()
    while node is not None and node.name != "h2":
        if node.name == "p":
            text = node.get_text(" ", strip=True)
            if _looks_like_page_chrome(text):
                # Real article content ends here -- everything after this
                # point in the concatenated blob is unrelated page chrome
                # (see _looks_like_page_chrome). Stop, don't include it.
                break
            strong = node.find("strong")
            strong_text = strong.get_text(strip=True) if strong else ""
            if strong_text.endswith(":") and text.startswith(strong_text):
                if current_paras:
                    turns.append((current_speaker, "\n\n".join(current_paras)))
                current_speaker = strong_text[:-1]
                remainder = text[len(strong_text):].strip()
                current_paras = [remainder] if remainder else []
            else:
                current_paras.append(text)
        node = node.find_next_sibling()

    if current_paras:
        turns.append((current_speaker, "\n\n".join(current_paras)))
    return turns


def render_transcript_text(ticker: str, company: str, url: str, soup: BeautifulSoup) -> str:
    call_date = _section_text(soup, "date")
    participants = _section_bullets(soup, "call-participants")
    takeaways = _section_bullets(soup, "takeaways")
    summary_para = _section_text(soup, "summary")
    summary_bullets = _section_bullets(soup, "summary")
    glossary = _section_bullets(soup, "industry-glossary")
    turns = extract_transcript_turns(soup)

    lines = [f"{company} ({ticker}) Earnings Call Transcript", f"Source: The Motley Fool — {url}"]
    if call_date:
        lines.append(f"Call date: {call_date}")
    lines += ["", "Call participants:"]
    lines += [f"- {p}" for p in participants]
    lines += ["", "TAKEAWAYS", ""]
    lines += [f"- {t}" for t in takeaways]
    lines += ["", "SUMMARY", ""]
    if summary_para:
        lines.append(summary_para)
        lines.append("")
    lines += [f"- {b}" for b in summary_bullets]
    lines += ["", "INDUSTRY GLOSSARY", ""]
    lines += [f"- {g}" for g in glossary]
    lines += ["", "Full Conference Call Transcript", ""]
    for speaker, text in turns:
        lines.append(f"{speaker}: {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class TranscriptQAFailure(Exception):
    """Raised by qa_gate() -- caught at the call site so one bad ticker
    doesn't take down the whole run, but the failure is always printed
    loudly, never silently swallowed."""


def qa_gate(text: str, ticker: str, min_turns: int = 5, min_chars: int = 5000) -> None:
    """Non-optional structural check, per the doc's own requirement: a
    garbled or wrong-page extraction must fail loudly and get flagged
    for manual review, never get silently saved and embedded as if it
    were a real transcript. This is what actually protects the product's
    citation-trustworthiness premise -- the automation itself doesn't.
    """
    problems = []
    if len(text) < min_chars:
        problems.append(f"too short ({len(text)} chars, expected >= {min_chars})")
    if "Call participants:" not in text:
        problems.append("missing 'Call participants:' section")
    if "Full Conference Call Transcript" not in text:
        problems.append("missing 'Full Conference Call Transcript' section header")
    speaker_lines = re.findall(r"^[A-Za-z][^:\n]{2,60}:", text, re.MULTILINE)
    if len(speaker_lines) < min_turns:
        problems.append(f"only {len(speaker_lines)} speaker-turn markers found (expected >= {min_turns})")
    if "operator" not in text.lower():
        problems.append("no 'Operator' mention found -- real earnings calls always have one")
    if _looks_like_page_chrome(text):
        problems.append(
            "leaked page-chrome/React-JSON content found in saved text -- extraction did not "
            "stop cleanly at the end of the real article (see _looks_like_page_chrome)"
        )
    if problems:
        raise TranscriptQAFailure(f"{ticker}: " + "; ".join(problems))


def ingest_transcript(ticker: str) -> str | None:
    """Full pipeline for one ticker: find URL -> fetch -> parse -> QA
    gate -> save. Returns the saved file path, or None if any step
    failed (always printed, never silent). This is the function
    fetch_ticker.py's single-entrypoint ingestion (item 3 step 5) calls.
    """
    ticker = ticker.upper()
    company = TICKER_TO_COMPANY.get(ticker, ticker)
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        print(f"!! {ticker}: TAVILY_API_KEY not configured -- cannot locate transcript URL")
        return None

    url = find_transcript_url(ticker, company, api_key)
    if not url:
        print(f"!! {ticker}: no Motley Fool transcript URL found via search")
        return None

    try:
        raw_html = fetch_page(url)
    except requests.RequestException as e:
        print(f"!! {ticker}: fetch failed for {url}: {e}")
        return None

    article_html = extract_article_html(raw_html)
    soup = BeautifulSoup(article_html, "html.parser")
    text = render_transcript_text(ticker, company, url, soup)

    try:
        qa_gate(text, ticker)
    except TranscriptQAFailure as e:
        print(f"!! QA GATE FAILED, NOT SAVED: {e}")
        print(f"   Source URL was: {url}")
        return None

    ticker_dir = os.path.join(DATA_DIR, ticker)
    os.makedirs(ticker_dir, exist_ok=True)
    # Filename period left generic ("latest") rather than parsed from the
    # page -- the existing 4 tickers' files use a Q#_YYYY pattern that
    # was filled in by hand; deriving that reliably from the page's
    # "date" section would need real calendar-quarter logic this
    # extraction doesn't have yet. Flagging as a known simplification,
    # not hidden: check the saved file's own "Call date:" line for the
    # real date, don't trust the filename to encode it correctly.
    dest_path = os.path.join(ticker_dir, "transcript_latest.txt")
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"OK  {ticker}: saved {dest_path} ({len(text)} chars, source: {url})")
    return dest_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Fetch just this one ticker instead of all of TICKERS.")
    args = parser.parse_args()

    targets = [args.ticker.upper()] if args.ticker else TICKERS
    results = {}
    for ticker in targets:
        results[ticker] = ingest_transcript(ticker)

    print()
    print("Summary:", {t: ("OK" if p else "FAILED") for t, p in results.items()})
    failed = [t for t, p in results.items() if not p]
    if failed:
        print(f"!! {len(failed)} ticker(s) need manual review: {failed}")


if __name__ == "__main__":
    main()
