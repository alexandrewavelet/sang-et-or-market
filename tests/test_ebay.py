import unittest
from pathlib import Path

from hoarder.sources.ebay import Blocked, parse_price, parse_search_html, parse_sold_date

FIX = Path(__file__).parent / "fixtures"


class EbayParseTest(unittest.TestCase):
    def test_scard_sold(self):
        items = parse_search_html((FIX / "ebay_sold_scard.html").read_text(), sold=True)
        self.assertEqual([i["source_id"] for i in items], ["356001", "356002"])  # promo + price range skipped
        udol, thauvin = items
        self.assertEqual(udol["title"], "2025-26 Panini Select Ligue 1 Matthieu Udol RC Lens Gold /10")
        self.assertEqual((udol["price"], udol["currency"], udol["sold_at"]), (45.0, "EUR", "2026-11-03"))
        self.assertEqual(udol["url"], "https://www.ebay.fr/itm/356001")
        self.assertEqual(thauvin["title"], "Florian Thauvin Topps Chrome RC Lens auto")
        self.assertEqual((thauvin["price"], thauvin["currency"], thauvin["sold_at"]), (120.0, "USD", "2026-09-12"))

    def test_legacy_sitem(self):
        (item,) = parse_search_html((FIX / "ebay_sold_sitem.html").read_text(), sold=True)
        self.assertEqual((item["price"], item["sold_at"]), (18.5, "2026-10-21"))

    def test_challenge_detected(self):
        with self.assertRaises(Blocked):
            parse_search_html("<html><title>Pardon Our Interruption...</title></html>", sold=True)

    def test_price_and_dates(self):
        self.assertEqual(parse_price("1 234,50 EUR"), (1234.5, "EUR"))
        self.assertEqual(parse_price("£9.99"), (9.99, "GBP"))
        self.assertEqual(parse_sold_date("Vendu le 1 déc. 2025"), "2025-12-01")


if __name__ == "__main__":
    unittest.main()
