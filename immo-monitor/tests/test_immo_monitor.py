import email
import email.policy
import sys
import tomllib
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import immo_monitor as im  # noqa: E402

CFG = tomllib.loads((Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 25)


def mail(subject, html):
    raw = f"Subject: {subject}\nFrom: Alert <noreply@immobilienscout24.de>\nMIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n{html}"
    return email.message_from_bytes(raw.encode("utf-8"), policy=email.policy.default)


def item(site, ext_id, title, body, redirect=False):
    url = f"https://www.{site}/expose/{ext_id}"
    href = f"https://click.example.com/r?u={quote(url, safe='')}" if redirect else url
    return f'<div><a href="{href}">{title}</a><br>{body}</div>'


WOHN = item("immobilienscout24.de", "123456789", "Helle 3-Zimmer-Wohnung am Park",
            "12345 Berlin Kaufpreis 300.000 € Wohnfläche 100 m² 3 Zi.", redirect=True)
GEW = item("immowelt.de", "2abc3def", "Büro- und Geschäftshaus",
           "10115 Berlin Kaufpreis 4.000.000 € Nutzfläche 1.500 m² Jahresnettomiete 450.000 €")
ZWANG = item("immobilienscout24.de", "555666777", "Wohnung - Zwangsversteigerung",
             "10115 Berlin 150.000 € 90 m² Amtsgericht Mitte Verkehrswertgutachten")
FOOT = '<a href="https://example.com/unsub">Abmelden</a> 99999 Nirgendwo 1 € 1 m²'


class ParseTests(unittest.TestCase):
    def parse(self, subject, *items):
        return im.parse_message(mail(subject, "<html><body>" + "".join(items) + FOOT + "</body></html>"), CFG)

    def test_fields_and_redirect(self):
        (l,) = self.parse("WSS-BER-WOHNEN: neue Treffer", WOHN)
        self.assertEqual((l.site, l.ext_id, l.url), ("immoscout", "123456789", "https://www.immobilienscout24.de/expose/123456789"))
        self.assertEqual((l.price, l.area, l.rooms, l.plz, l.city), (300000.0, 100.0, 3.0, "12345", "Berlin"))
        self.assertEqual((l.kind, l.region, l.ptype), ("wohnen", "Berlin", "wohnung"))
        self.assertAlmostEqual(l.ppsqm, 3000.0)
        self.assertNotIn("Nirgendwo", l.chunk)  # Footer nach Fremdlink gehoert nicht zum Inserat

    def test_gewerbe_factor_from_rent(self):
        (l,) = self.parse("WSS-BER-GEWERBE", GEW)
        self.assertEqual((l.site, l.kind, l.area, l.price), ("immowelt", "gewerbe", 1500.0, 4000000.0))
        self.assertAlmostEqual(l.factor, 4000000 / 450000)

    def test_zwangsversteigerung_flagged(self):
        (l,) = self.parse("WSS-BER-WOHNEN", ZWANG)
        self.assertTrue(l.zwang)

    def test_plain_text_mail(self):
        raw = ("Subject: WSS-DUS-WOHNEN\nContent-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n"
               "Reihenhaus in Neuss\n40213 Düsseldorf 500.000 € 125 m²\nhttps://www.immowelt.de/expose/9zz88yy7\n\n"
               "Abmelden https://example.com/u\n")
        # Text steht hier VOR dem Link -> bekanntes Limit des Parsers
        (l,) = im.parse_message(email.message_from_bytes(raw.encode("utf-8"), policy=email.policy.default), CFG)
        self.assertEqual(l.ext_id, "9zz88yy7")


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.db = im.open_db(":memory:")
        # Historie: 10 Wohnungen in Berlin 123xx bei ~5000 EUR/m2
        for i in range(10):
            l = im.Listing("immoscout", f"h{i}", "u", "", kind="wohnen", region="Berlin", ptype="wohnung",
                           plz="12345", price=500000 + i * 5000, area=100)
            im.upsert(self.db, l, "2026-10-01")
            self.db.execute("UPDATE listings SET reported=1")

    def run_mail(self, subject, *items):
        msg = mail(subject, "".join(items))
        rows, stats = im.run(CFG, [msg], self.db, TODAY)
        return rows, stats

    def test_deal_detected(self):
        rows, stats = self.run_mail("WSS-BER-WOHNEN", WOHN)
        (r,) = rows
        self.assertTrue(r.get("deal"))
        self.assertLess(r["ratio"], 0.7)
        self.assertEqual(stats["new"], 1)

    def test_zwang_excluded_and_counted(self):
        rows, stats = self.run_mail("WSS-BER-WOHNEN", ZWANG)
        self.assertEqual(rows, [])
        self.assertEqual(stats["zwang"], 1)

    def test_gewerbe_included_and_factor_filter(self):
        rows, _ = self.run_mail("WSS-BER-GEWERBE", GEW)
        self.assertEqual(len(rows), 1)
        teuer = item("immowelt.de", "3xyz9abc", "Lagerhalle", "10115 Berlin Kaufpreis 9.000.000 € 2.000 m² Jahresnettomiete 450.000 €")
        rows, stats = self.run_mail("WSS-BER-GEWERBE", teuer)  # Faktor 20 > 10
        self.assertEqual(rows, [])
        self.assertEqual(stats["gewerbe_out"], 1)

    def test_gewerbe_too_small_and_unknown_factor(self):
        klein = item("immowelt.de", "4small11", "Büro", "10115 Berlin 500.000 € 400 m²")
        unbekannt = item("immowelt.de", "5unkn222", "Lagerhalle", "10115 Berlin 2.000.000 € 1.200 m²")
        rows, _ = self.run_mail("WSS-BER-GEWERBE", klein, unbekannt)
        self.assertEqual([r["ext_id"] for r in rows], ["5unkn222"])
        self.assertIn("Faktor unbekannt", rows[0]["flags"][0])

    def test_idempotent_and_price_drop(self):
        self.run_mail("WSS-BER-WOHNEN", WOHN)
        rows, stats = self.run_mail("WSS-BER-WOHNEN", WOHN)
        self.assertEqual((rows, stats["new"]), ([], 0))
        billiger = WOHN.replace("300.000", "250.000")
        rows, stats = self.run_mail("WSS-BER-WOHNEN", billiger)
        self.assertEqual(stats["reduced"], 1)
        self.assertEqual(rows[0]["price_prev"], 300000.0)

    def test_report_renders(self):
        rows, stats = self.run_mail("WSS-BER-WOHNEN", WOHN, GEW)
        text = im.render_report(rows, stats, TODAY, 40)
        self.assertIn("Top-Deals Wohnen (1)", text)
        self.assertIn("https://www.immobilienscout24.de/expose/123456789", text)


if __name__ == "__main__":
    unittest.main()
