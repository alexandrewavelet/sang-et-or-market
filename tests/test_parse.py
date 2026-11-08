import json
import unittest
from pathlib import Path

from hoarder.parse import (PlayerMatcher, analyse_text, extract_numbering, extract_product,
                           extract_season, numbering_band)

CONFIG = json.loads((Path(__file__).parent.parent / "config.json").read_text())
MATCHER = PlayerMatcher(CONFIG["players"])
KW = CONFIG["club"]["keywords"]


class NumberingTest(unittest.TestCase):
    def test_cases(self):
        cases = {
            "Carte de Matthieu Udol numérotée sur 4 !": (4, None),
            "Abdul samed /65 score panini RC Lens": (65, None),
            "Saud Abdulhamid 2026 Panini Prizm Orange Pandora RC /215 #384": (215, None),
            "Thauvin gold 03/10 Select 2025-26": (10, 3),
            "Sotoca 1/1 black": (1, 1),
            "Panini Foot 2026/2027 Lens": (None, None),
            "Adrenalyn XL 25/26 Udol": (None, None),
            "Adrenalyn 26-27 n°409": (None, None),
            "Openda numérotée 12 / 49": (49, 12),
            "Topps chrome lens numbered to 99": (99, None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_numbering(text), expected)

    def test_band(self):
        self.assertEqual(numbering_band(4), "/2-5")
        self.assertEqual(numbering_band(1), "1/1")
        self.assertEqual(numbering_band(65), "/51-99")


class SeasonProductTest(unittest.TestCase):
    def test_season(self):
        self.assertEqual(extract_season("Panini Select Ligue 1 2025-26"), "2025-26")
        self.assertEqual(extract_season("Adrenalyn XL 26/27"), "2026-27")
        self.assertEqual(extract_season("Panini Foot 2026/2027"), "2026-27")
        self.assertEqual(extract_season("Ligue 1 2027"), "2026-27")

    def test_product(self):
        self.assertEqual(extract_product("Panini select RC Lens"), "Panini Select")
        self.assertEqual(extract_product("PANINI ADRENALYN XL 2026-27"), "Adrenalyn XL")
        self.assertEqual(extract_product("Topps Chrome Ligue 1 Thauvin"), "Topps Chrome")
        self.assertEqual(extract_product("Panini UNFP Foot Cards 98"), "Panini Foot (stickers)")


class AnalyseTest(unittest.TestCase):
    def test_udol_listing(self):
        info = analyse_text(
            "Panini select RC Lens",
            "Carte de Matthieu Udol numérotée sur 4 ! Carte pour collectionneur",
            MATCHER, KW,
        )
        self.assertEqual(info.player, "Matthieu Udol")
        self.assertEqual(info.product, "Panini Select")
        self.assertEqual(info.print_run, 4)
        self.assertTrue(info.club_context)
        self.assertFalse(info.is_lot)

    def test_lot_and_auto(self):
        lot = analyse_text("Lot 11 Panini Adrenalyn XL RC Lens", "", MATCHER, KW)
        self.assertTrue(lot.is_lot)
        auto = analyse_text("Carte de Florian Thauvin signée", "", MATCHER, KW)
        self.assertEqual(auto.player, "Florian Thauvin")
        self.assertTrue(auto.is_auto)

    def test_rookie_not_rc_lens(self):
        self.assertFalse(analyse_text("Panini select RC Lens", "", MATCHER, KW).is_rookie)
        self.assertTrue(analyse_text("Ouattara Select RC /55", "", MATCHER, KW).is_rookie)

    def test_numbered_without_run(self):
        info = analyse_text("Amadou Haidara Gold Numbered Rookie Card Lens", "", MATCHER, KW)
        self.assertIsNone(info.print_run)
        self.assertEqual(info.band, "numérotée (tirage ?)")

    def test_club_false_positive(self):
        self.assertFalse(analyse_text("Michael Olise 3D X-Lens Phase Breaker Topps", "", MATCHER, KW).club_context)
        self.assertTrue(analyse_text("Carte Sotoca RC Lens", "", MATCHER, KW).club_context)

    def test_sticker_and_swatches(self):
        self.assertTrue(analyse_text("Panini, Foot 2011, N° 154, ISSAM JEMAA, RC Lens", "", MATCHER, KW).is_sticker)
        self.assertFalse(analyse_text("Panini Select Ligue 1 Udol Select Swatches /4 Lens", "", MATCHER, KW).is_sticker)
        self.assertTrue(analyse_text("Panini Select Ligue 1 Udol Select Swatches Lens", "", MATCHER, KW).is_patch)

    def test_non_card_excluded(self):
        self.assertFalse(analyse_text("Flocage Officiel - RC Lens, Sotoca, 2022/2023", "", MATCHER, KW).club_context)

    def test_player_aliases(self):
        self.assertEqual(MATCHER.match("carte saint maximin lens")["name"], "Allan Saint-Maximin")
        self.assertEqual(MATCHER.match("UDOL crack")["name"], "Matthieu Udol")


if __name__ == "__main__":
    unittest.main()
