import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from hoarder.db import DB, now
from hoarder.env import load_dotenv, parse_dotenv
from hoarder.fetch import FetchError
from hoarder.scrape import run_ebay_api
from hoarder.sources.ebay import ApiError, Ebay, api_item


def summary(n: int, **kw) -> dict:
    return {"itemId": f"v1|{n}|0", "legacyItemId": str(n), "title": f"Panini RC Lens card {n}",
            "price": {"value": "12.50", "currency": "EUR"}, "buyingOptions": ["FIXED_PRICE"],
            "itemWebUrl": f"https://www.ebay.fr/itm/{n}?hash=item123&amdata=x",
            "image": {"imageUrl": f"https://i.ebayimg.com/{n}.jpg"}, "condition": "Neuf",
            "seller": {"username": "someone", "feedbackScore": 12}, **kw}


class FakeClient:
    """Stands in for fetch.Client: serves a token, paginated search results and item lookups."""

    def __init__(self, total: int = 0, live: set[str] | None = None, token_status: int | None = None):
        self.total, self.live, self.token_status = total, live or set(), token_status
        self.calls: list[str] = []
        self.requests = 0
        self.groups: set[str] = set()

    def get(self, url: str, headers=None, data=None) -> str:
        self.calls.append(url)
        if "oauth2/token" in url:
            if self.token_status:
                raise FetchError(self.token_status, url)
            return json.dumps({"access_token": "tok", "expires_in": 7200})
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "item_summary/search" in url:
            off, lim = int(q["offset"]), int(q["limit"])
            items = [summary(1000 + i) for i in range(off, min(off + lim, self.total))]
            page = {"total": self.total, "itemSummaries": items}
            if off + lim < self.total:
                page["next"] = "…"
            return json.dumps(page)
        if "get_items_by_item_group" in url:
            return json.dumps({"items": [{"itemId": "x"}]})
        if "get_item_by_legacy_id" in url:
            if q["legacy_item_id"] in self.groups:
                raise FetchError(400, url, '{"errors":[{"errorId":11006,"message":"use item group"}]}')
            if q["legacy_item_id"] in self.live:
                return json.dumps({"itemId": "x", "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}]})
            raise FetchError(404, url)
        raise AssertionError(url)


class NetworkRetryTest(unittest.TestCase):
    def test_timeouts_are_retried_then_become_fetch_error(self):
        import urllib.error
        from hoarder.fetch import Client
        c = Client("t", 0)
        with mock.patch.object(c, "_get", side_effect=[urllib.error.URLError("timed out"), "ok"]), \
                mock.patch("hoarder.fetch.time.sleep"):
            self.assertEqual(c.get("https://x"), "ok")
        with mock.patch.object(c, "_get", side_effect=TimeoutError("t")), mock.patch("hoarder.fetch.time.sleep"), \
                self.assertRaises(FetchError) as ctx:
            c.get("https://x")
        self.assertEqual(ctx.exception.status, 0)


class DotenvTest(unittest.TestCase):
    def test_parse(self):
        env = parse_dotenv('# c\n\nA=1\nexport B="two words"\nC=\'x\'\nD=val # note\nE=\nnot a line\n')
        self.assertEqual(env, {"A": "1", "B": "two words", "C": "x", "D": "val", "E": ""})

    def test_load_does_not_override_and_skips_blank(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HOARDER_T1": "shell"}, clear=False):
            p = Path(d) / ".env"
            p.write_text("HOARDER_T1=file\nHOARDER_T2=file\nHOARDER_T3=\n")
            self.assertEqual(load_dotenv(p), ["HOARDER_T2"])
            self.assertEqual(os.environ["HOARDER_T1"], "shell")
            self.assertEqual(os.environ["HOARDER_T2"], "file")
            self.assertNotIn("HOARDER_T3", os.environ)
            os.environ.pop("HOARDER_T2")
        self.assertEqual(load_dotenv(Path("/nonexistent/.env")), [])


class ApiItemTest(unittest.TestCase):
    def test_fixed_price_uses_legacy_id_and_clean_url(self):
        item = api_item(summary(365963881285))
        self.assertEqual(item["source_id"], "365963881285")  # same id as the HTML import path -> one row
        self.assertEqual(item["url"], "https://www.ebay.fr/itm/365963881285")
        self.assertEqual((item["price"], item["currency"], item["status"]), (12.5, "EUR", "active"))
        self.assertNotIn("seller", json.dumps(item))  # no eBay user data persisted
        self.assertNotIn("description", item)

    def test_auction_uses_current_bid_and_marker(self):
        item = api_item(summary(5, buyingOptions=["AUCTION"], price=None,
                                currentBidPrice={"value": "3.00", "currency": "EUR"}))
        self.assertEqual((item["price"], item["description"]), (3.0, "[enchère en cours]"))

    def test_fallbacks_and_skips(self):
        it = summary(7)
        del it["legacyItemId"]
        self.assertEqual(api_item(it)["source_id"], "7")
        self.assertIsNone(api_item(summary(8, price=None)))  # no price -> not comparable


class ApiSearchTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_paginates_and_caps(self):
        client = FakeClient(total=450)
        ebay = Ebay(client, {"api_category_ids": [261328]})
        self.assertEqual(len(list(ebay.api_search("rc lens", 1000))), 450)
        searches = [c for c in client.calls if "search" in c]
        self.assertEqual(len(searches), 3)
        self.assertIn("category_ids=261328", searches[0])
        self.assertEqual(sum("oauth2" in c for c in client.calls), 1)  # token reused
        self.assertEqual(len(list(Ebay(FakeClient(total=450), {}).api_search("x", 200))), 200)

    def test_bad_credentials_raise_clear_error_after_retries(self):
        client = FakeClient(token_status=401)
        with mock.patch("hoarder.sources.ebay.time.sleep"), self.assertRaisesRegex(ApiError, "EBAY_CLIENT_ID"):
            list(Ebay(client, {}).api_search("x"))
        self.assertEqual(len(client.calls), 3)

    def test_spurious_token_401_is_retried(self):
        client = FakeClient(total=1)
        real_get = client.get
        failures = iter([True])

        def flaky(url, headers=None, data=None):
            if "oauth2" in url and next(failures, False):
                client.calls.append(url)
                raise FetchError(401, url)
            return real_get(url, headers, data)
        client.get = flaky
        with mock.patch("hoarder.sources.ebay.time.sleep"):
            self.assertEqual(len(list(Ebay(client, {}).api_search("x"))), 1)


class RunEbayApiTest(unittest.TestCase):
    def test_import_then_mark_vanished_as_ended(self):
        cfg = {"http": {"user_agent": "t", "timeout_seconds": 5},
               "ebay": {"api_queries": ["rc lens"], "fx_to_eur": {"EUR": 1.0}}}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "s"}):
            db = DB(Path(d) / "t.db")
            # Pre-existing eBay rows (e.g. from an HTML import): one still live, one gone.
            for sid in ("900", "901", "902"):
                db.upsert({"source": "ebay", "source_id": sid, "title": "old", "price": 5.0}, "2026-01-01T00:00:00+00:00")
            fake = FakeClient(total=3, live={"900"})
            fake.groups = {"902"}  # live multi-variation listing
            with mock.patch("hoarder.scrape.Client", return_value=fake):
                stats = {"new": 0, "seen": 0, "errors": 0}
                api_stats = run_ebay_api(cfg, db, now(), stats)
            self.assertEqual((stats["new"], api_stats["items"], api_stats["checked"], api_stats["ended"]), (3, 3, 3, 1))
            status = {r["source_id"]: r["status"] for r in db.all()}
            self.assertEqual(status, {"900": "active", "901": "ended", "902": "active", "1000": "active", "1001": "active", "1002": "active"})


if __name__ == "__main__":
    unittest.main()
