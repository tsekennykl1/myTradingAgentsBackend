"""Stock directory: HK + US codes, names and market in one table."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
ABCDW|ABCD Acquisition Corp - Warrant|S|N|N|100|N|N
ZXZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N
File Creation Time: 1023202610:01|||||||
"""

OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK.B|N|100|N|BRK=B
ABR$D|Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock|N|ABRpD|N|100|N|ABR-D
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
File Creation Time: 1023202610:01||||||
"""


@pytest.fixture()
def directory(tmp_path, monkeypatch):
    from app import securities, us_securities

    monkeypatch.setattr(securities, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(us_securities, "CACHE_DIR", tmp_path / "sources")
    monkeypatch.setattr(securities, "_loaded", set())
    pages = {us_securities.NASDAQ_LISTED_URL: NASDAQ, us_securities.OTHER_LISTED_URL: OTHER}

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(url, **_):
        calls.append(url)
        if pages.get("offline"):
            raise ConnectionError("offline")
        return FakeResponse(pages[url])

    monkeypatch.setattr(us_securities.requests, "get", fake_get)
    return securities, pages, calls


def test_us_rows_map_code_name_and_market(directory):
    securities, _, _ = directory
    assert securities.ensure_loaded(["US"]) == {"US": 4}  # AAPL, QQQ, BRK-B, SPY
    apple = securities.lookup("aapl")
    assert apple == {
        "ticker": "AAPL",
        "code": "AAPL",
        "name_en": "Apple Inc.",
        "name_zh": None,
        "market": "US",
        "exchange": "NASDAQ",
        "category": "Equity",
    }


def test_share_class_codes_use_price_data_ticker(directory):
    securities, _, _ = directory
    row = securities.lookup("BRK.B")
    assert row["ticker"] == "BRK-B" and row["code"] == "BRK.B" and row["exchange"] == "NYSE"
    assert securities.lookup("brk-b")["name_en"] == "Berkshire Hathaway Inc. New"


def test_test_issues_warrants_and_preferreds_are_excluded(directory):
    securities, _, _ = directory
    tickers = {row["ticker"] for row in securities.listing("US")}
    assert tickers == {"AAPL", "QQQ", "BRK-B", "SPY"}


def test_saved_copy_is_used_when_nasdaq_is_unreachable(directory, monkeypatch):
    securities, pages, _ = directory
    from app import us_securities

    securities.ensure_loaded(["US"])
    monkeypatch.setattr(us_securities, "MAX_AGE_SECONDS", 0)  # force a re-download
    monkeypatch.setattr(securities, "_loaded", set())
    pages["offline"] = True
    assert securities.ensure_loaded(["US"]) == {"US": 4}


def test_hk_rows_share_the_table_with_market_hk(directory):
    securities, _, _ = directory
    securities.ensure_loaded(["HK"])
    tencent = securities.lookup("700")
    assert tencent["ticker"] == "0700.HK" and tencent["code"] == "00700"
    assert tencent["market"] == "HK" and tencent["name_zh"] == "騰訊控股"


def test_numbers_are_hk_and_letters_are_us(directory):
    securities, _, _ = directory
    assert securities.market_of("0700.HK") == "HK"
    assert securities.market_of("9988") == "HK"
    assert securities.market_of("TSLA") == "US"
    assert securities.market_of("7203.T") is None


def test_search_puts_code_prefix_matches_first(directory):
    securities, _, _ = directory
    results = securities.search("S", "US")
    assert results[0]["ticker"] == "SPY"


def test_full_us_list_endpoint_for_keypress_suggestions(directory):
    from app.routes.market import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/market/securities?market=US")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    assert body["securities"]["AAPL"] == ["Apple Inc.", None, "NASDAQ"]

    raw = client.get(
        "/market/securities?market=US", headers={"Accept-Encoding": "gzip"}
    )
    assert raw.headers.get("content-encoding") == "gzip"
    again = client.get("/market/securities?market=US", headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304

    assert client.get("/market/security?ticker=AAPL").json()["market"] == "US"
    assert client.get("/market/security?ticker=NOPE").json()["available"] is False
    assert client.get("/market/securities?market=JP").status_code == 400


def test_us_ticker_normalization():
    from app.us_securities import clean_name, normalize_ticker

    assert normalize_ticker("brk.b") == "BRK-B"
    assert normalize_ticker("AAPL") == "AAPL"
    assert normalize_ticker("0700") is None
    assert clean_name("ATA Creativity Global - American Depositary Shares, each representing two") == "ATA Creativity Global"
