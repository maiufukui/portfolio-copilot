"""
Test suite for fetch_transcripts.py.

Two tiers, same split as test_db.py:

  1. Unit tests against small synthetic HTML fixtures -- fast, no
     dependency on any real saved page, cover the parsing rules
     directly (speaker-turn detection, continuation paragraphs, page-
     chrome contamination detection).
  2. A real integration test against this project's own saved ALAB page
     (Data/ALAB/Astera Labs (ALAB) Q1 2026 Earnings Transcript _ The
     Motley Fool.html) -- the actual ground truth this whole module's
     extraction logic was built and debugged against. This is what
     originally caught a real bug (leaked React-JSON page-chrome
     content in the output) during manual verification; formalized here
     so that bug can't silently come back. Skips cleanly if the file
     isn't present rather than failing the whole suite.

Does NOT test find_transcript_url or fetch_page -- both require live
network (Tavily, fool.com) this dev sandbox's outbound allowlist has
confirmed it can't reach. Verify those for real locally:

    python fetch_transcripts.py --ticker PANW

Run:
    pytest test_fetch_transcripts.py -v
"""

from __future__ import annotations

import os

import pytest
from bs4 import BeautifulSoup

from fetch_transcripts import (
    TranscriptQAFailure,
    _looks_like_page_chrome,
    extract_article_html,
    extract_transcript_turns,
    qa_gate,
    render_transcript_text,
)

ALAB_HTML_PATH = os.path.join(
    "Data", "ALAB", "Astera Labs (ALAB) Q1 2026 Earnings Transcript _ The Motley Fool.html"
)

# ================================================================= unit ===


class TestLooksLikePageChrome:
    def test_real_transcript_text_is_not_flagged(self):
        assert not _looks_like_page_chrome(
            "Jitendra Mohan: Thank you, Leslie. Good afternoon, everyone."
        )

    def test_react_json_element_is_flagged(self):
        assert _looks_like_page_chrome('["$","p","102",{"className":"$undefined","children":"x"}]')

    def test_article_metadata_json_is_flagged(self):
        assert _looks_like_page_chrome('"articleKeys":["disclosure","instruments"]')


class TestExtractTranscriptTurns:
    def _soup(self, inner_html: str) -> BeautifulSoup:
        html = f'<h2 id="full-conference-call-transcript">Full Conference Call Transcript</h2>{inner_html}'
        return BeautifulSoup(html, "html.parser")

    def test_single_turn(self):
        soup = self._soup("<p><strong>Jitendra Mohan:</strong> Thank you, Leslie.</p>")
        turns = extract_transcript_turns(soup)
        assert turns == [("Jitendra Mohan", "Thank you, Leslie.")]

    def test_continuation_paragraphs_join_the_prior_turn(self):
        soup = self._soup(
            "<p><strong>Jitendra Mohan:</strong> First paragraph.</p>"
            "<p>Second paragraph, no speaker marker.</p>"
            "<p>Third paragraph, still no marker.</p>"
        )
        turns = extract_transcript_turns(soup)
        assert len(turns) == 1
        speaker, text = turns[0]
        assert speaker == "Jitendra Mohan"
        assert "First paragraph." in text
        assert "Second paragraph, no speaker marker." in text
        assert "Third paragraph, still no marker." in text

    def test_new_speaker_starts_a_new_turn(self):
        soup = self._soup(
            "<p><strong>Jitendra Mohan:</strong> My turn.</p>"
            "<p><strong>Operator:</strong> Next question.</p>"
        )
        turns = extract_transcript_turns(soup)
        assert [t[0] for t in turns] == ["Jitendra Mohan", "Operator"]

    def test_leading_unmarked_paragraphs_are_unattributed_intro(self):
        soup = self._soup(
            "<p>Housekeeping legal disclaimer text with no speaker marker.</p>"
            "<p><strong>Jitendra Mohan:</strong> Now the real content starts.</p>"
        )
        turns = extract_transcript_turns(soup)
        assert turns[0][0] == "(unattributed intro)"
        assert turns[1][0] == "Jitendra Mohan"

    def test_stops_at_page_chrome_contamination(self):
        """Regression test for the real bug found during manual
        verification: real article content ends, but the concatenated
        blob keeps going into unrelated page-chrome chunks (promo
        widgets, disclosure metadata) serialized as raw React-element
        JSON rather than real HTML. Extraction must stop there, not
        include it."""
        soup = self._soup(
            "<p><strong>Operator:</strong> This concludes the call.</p>"
            '<p>["$","p","102",{"className":"$undefined","children":"leaked page chrome"}]</p>'
            "<p><strong>Should Not Appear:</strong> this turn is past the contamination point.</p>"
        )
        turns = extract_transcript_turns(soup)
        assert len(turns) == 1
        assert turns[0][0] == "Operator"
        assert "Should Not Appear" not in str(turns)


class TestQAGate:
    GOOD_TEXT = (
        "Company (TICK) Earnings Call Transcript\n"
        "Source: The Motley Fool — https://example.com\n\n"
        "Call participants:\n- CEO — Someone\n\n"
        "Full Conference Call Transcript\n\n"
        + "".join(
            f"Speaker{i}: Some real paragraph of transcript content about the business, repeated "
            "enough times to clear the QA gate's minimum length check realistically. Operator note.\n\n"
            for i in range(60)
        )
    )

    def test_good_transcript_passes(self):
        qa_gate(self.GOOD_TEXT, "TICK")  # should not raise

    def test_too_short_fails(self):
        with pytest.raises(TranscriptQAFailure, match="too short"):
            qa_gate("Speaker: hi. Operator: bye.", "TICK")

    def test_missing_participants_section_fails(self):
        text = self.GOOD_TEXT.replace("Call participants:", "Nothing here:")
        with pytest.raises(TranscriptQAFailure, match="Call participants"):
            qa_gate(text, "TICK")

    def test_missing_operator_mention_fails(self):
        text = self.GOOD_TEXT.replace("Operator", "Moderator")
        with pytest.raises(TranscriptQAFailure, match="Operator"):
            qa_gate(text, "TICK")

    def test_leaked_page_chrome_fails(self):
        text = self.GOOD_TEXT + '\n["$","p","1",{"className":"$undefined"}]'
        with pytest.raises(TranscriptQAFailure, match="leaked page-chrome"):
            qa_gate(text, "TICK")


# ========================================================= integration ===


@pytest.mark.skipif(not os.path.exists(ALAB_HTML_PATH), reason="Real saved ALAB page not present")
class TestAgainstRealSavedPage:
    """The actual ground truth this module's extraction logic was built
    and debugged against -- not a synthetic fixture. If Motley Fool's
    page structure changes, or a future edit regresses the extraction,
    this is what would catch it."""

    @staticmethod
    @pytest.fixture(scope="class")
    def rendered_text():
        with open(ALAB_HTML_PATH, encoding="utf-8", errors="replace") as f:
            raw_html = f.read()
        article_html = extract_article_html(raw_html)
        soup = BeautifulSoup(article_html, "html.parser")
        return render_transcript_text(
            "ALAB",
            "Astera Labs",
            "https://www.fool.com/earnings/call-transcripts/2026/05/05/astera-labs-alab-q1-2026-earnings-transcript/",
            soup,
        )

    def test_passes_qa_gate(self, rendered_text):
        qa_gate(rendered_text, "ALAB")  # should not raise

    def test_contains_real_participants(self, rendered_text):
        assert "Jitendra Mohan" in rendered_text
        assert "Call participants:" in rendered_text

    def test_no_leaked_page_chrome(self, rendered_text):
        assert not _looks_like_page_chrome(rendered_text)

    def test_reasonable_length(self, rendered_text):
        # Real reference file (Data/ALAB/transcript_Q1_2026.txt) is
        # ~50,751 chars -- within a wide tolerance, not an exact match
        # (cosmetic differences: filename/header lines, 2 substitute
        # analysts labeled generically as "Analyst" rather than by name
        # -- see fetch_transcripts.py's docstring for why that's a known,
        # disclosed, non-blocking gap).
        assert 40000 < len(rendered_text) < 65000

    def test_ends_with_real_disclosure_footer_not_garbage(self, rendered_text):
        assert "Motley Fool" in rendered_text[-500:]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
