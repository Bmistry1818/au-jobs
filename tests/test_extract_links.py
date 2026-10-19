from __future__ import annotations

import pytest

from aujobs import extract
from aujobs.config import USER_AGENT
from aujobs.extract import ExtractError

PAGE = "https://www.jobsandskills.gov.au/data/occupation-and-industry-profiles"


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, url: str, **_: object) -> _Response:
        return _Response(self._text)


PAGE_WITH_TWO_WORKBOOKS = (
    '<a href="/sites/default/files/2026-07/osca_occupation_data_-_2021_census.xlsx">OSCA</a>'
    '<a href="/sites/default/files/2026-07/ANZSCO%20Occupation%20data%20-%20February%202026.xlsx">'
    "ANZSCO</a>"
)


def test_must_contain_selects_the_anzsco_workbook_not_the_first_link() -> None:
    session = _Session(PAGE_WITH_TWO_WORKBOOKS)

    url = extract.find_xlsx_link(session, PAGE, must_contain="anzsco")  # type: ignore[arg-type]

    assert "anzsco" in url.lower()
    assert "osca" not in url.lower()


def test_without_must_contain_the_first_workbook_is_returned() -> None:
    session = _Session(PAGE_WITH_TWO_WORKBOOKS)

    url = extract.find_xlsx_link(session, PAGE)  # type: ignore[arg-type]

    assert "osca" in url.lower()


def test_missing_workbook_names_what_was_wanted() -> None:
    session = _Session("<p>nothing here</p>")

    with pytest.raises(ExtractError, match="containing 'anzsco'"):
        extract.find_xlsx_link(session, PAGE, must_contain="anzsco")  # type: ignore[arg-type]


def test_user_agent_has_no_url_because_the_site_drops_requests_that_carry_one() -> None:
    assert "://" not in USER_AGENT
    assert "au-jobs" in USER_AGENT
