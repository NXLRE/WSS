#!/usr/bin/env python3
"""immo_monitor – wertet ImmoScout24- und Immowelt-Suchauftrags-Mails aus.

Ablauf: Postfach (IMAP) lesen -> Inserate aus den Alert-Mails parsen ->
in SQLite sammeln -> neue Inserate filtern/bewerten -> Tagesreport
(Markdown + CSV, optional per SMTP verschicken).

Nur Standardbibliothek (Python >= 3.11). Zugangsdaten ausschliesslich ueber
Umgebungsvariablen: IMAP_HOST, IMAP_USER, IMAP_PASSWORD und fuer --send
SMTP_HOST, SMTP_USER, SMTP_PASSWORD, MAIL_TO.
"""
from __future__ import annotations

import argparse
import csv
import email
import email.policy
import imaplib
import os
import re
import smtplib
import sqlite3
import statistics
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

EXPOSE_RE = re.compile(
    r"(immobilienscout24\.de|immowelt\.de)/expose/([0-9a-z]{6,})", re.IGNORECASE
)
EURO_RE = re.compile(
    r"(?:€\s*(\d{1,3}(?:\.\d{3})+|\d+)|(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?\s*(?:€|EUR\b|Euro\b))(?!\s*/\s*m)"
)
AREA_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?)\s*(?:m²|m2|qm)", re.IGNORECASE)
ROOMS_RE = re.compile(r"(\d+(?:,\d)?)\s*(?:Zi\.|Zimmer|Zi\b)", re.IGNORECASE)
PLZ_RE = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-\.]+(?: (?!Kaufpreis|Preis|Wohnfl|Nutzfl|Fläche|Zimmer|Zi\b|Grundst)[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-\.]+)?)")
FACTOR_RE = re.compile(r"Kaufpreisfaktor\D{0,15}(\d+(?:,\d+)?)", re.IGNORECASE)
YEAR_RENT_RE = re.compile(
    r"(?:Jahresnettomiete|Jahresmiete|Jahresnettokaltmiete|Mieteinnahmen[^\d]{0,12}(?:p\.\s?a\.|jährlich))\D{0,15}(\d{1,3}(?:\.\d{3})+|\d+)",
    re.IGNORECASE,
)
MONTH_RENT_RE = re.compile(
    r"(?:Ist-Miete|Nettokaltmiete|Mieteinnahmen)[^\d]{0,20}(?:mtl\.?|monatlich)?\D{0,10}(\d{1,3}(?:\.\d{3})+|\d+)",
    re.IGNORECASE,
)
ZWANG_RE = re.compile(
    r"zwangsversteiger|versteigerung|verkehrswertgutachten|amtsgericht|§\s?74a|aktenzeichen|teilungsversteigerung",
    re.IGNORECASE,
)
GEWERBE_RE = re.compile(
    r"büro|gewerbe|halle\b|lager|laden|praxis|gastronomie|logistik|produktion|geschäftshaus|renditeobjekt|handel|werkstatt|hotel",
    re.IGNORECASE,
)
HAUS_RE = re.compile(
    r"haus\b|einfamilienhaus|zweifamilienhaus|mehrfamilienhaus|reihenhaus|doppelhaushälfte|villa|bungalow|townhouse",
    re.IGNORECASE,
)


def de_number(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


# --------------------------------------------------------------------------
# Mail -> Inserate
# --------------------------------------------------------------------------
class _TextWithLinks(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "td"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            self.parts.append(f"\x00{dict(attrs).get('href') or ''}\x00")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _body_with_markers(msg) -> str:
    """Mailtext, in dem jeder Link als \\x00url\\x00 markiert ist."""
    part = msg.get_body(preferencelist=("html", "plain"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_subtype() == "html":
        p = _TextWithLinks()
        p.feed(content)
        return "".join(p.parts)
    return re.sub(r"https?://[^\s<>\"]+", lambda m: f"\x00{m.group(0)}\x00", content)


def _expose_ref(href: str):
    # Alert-Mails nutzen oft Redirect-Links, in denen die Expose-URL kodiert steckt.
    for _ in range(3):
        m = EXPOSE_RE.search(href)
        if m:
            return m.group(1).lower(), m.group(2).lower()
        href = unquote(href)
    return None


@dataclass
class Listing:
    site: str
    ext_id: str
    url: str
    chunk: str
    subject: str = ""
    profile: str = ""
    kind: str = ""
    region: str = ""
    ptype: str = ""
    title: str = ""
    plz: str = ""
    city: str = ""
    price: float | None = None
    area: float | None = None
    rooms: float | None = None
    factor: float | None = None
    zwang: bool = False

    @property
    def key(self) -> str:
        return f"{self.site}:{self.ext_id}"

    @property
    def ppsqm(self) -> float | None:
        return self.price / self.area if self.price and self.area else None


def split_listings(text: str) -> list[Listing]:
    chunks: dict[str, list[str]] = {}
    meta: dict[str, tuple[str, str]] = {}
    current = None
    for piece in re.split(r"(\x00[^\x00]*\x00)", text):
        if piece.startswith("\x00"):
            ref = _expose_ref(piece.strip("\x00"))
            if ref:
                current = f"{ref[0]}:{ref[1]}"
                meta[current] = ref
                chunks.setdefault(current, [])
            else:
                current = None  # sonstiger Link (Abmelden, Footer ...) beendet das Inserat
        elif current:
            chunks[current].append(piece)
    out = []
    for key, parts in chunks.items():
        site_dom, ext_id = meta[key]
        site = "immoscout" if "scout" in site_dom else "immowelt"
        url = f"https://www.{site_dom}/expose/{ext_id}"
        out.append(Listing(site, ext_id, url, "".join(parts)))
    return out


def extract_fields(l: Listing, cfg: dict) -> None:
    text = re.sub(r"[ \t\xa0]+", " ", l.chunk)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    flat = " ".join(lines)

    l.zwang = bool(ZWANG_RE.search(flat)) or bool(ZWANG_RE.search(l.subject))

    euros = []
    for m in EURO_RE.finditer(flat):
        euros.append((m.start(), de_number(m.group(1) or m.group(2))))
    labelled = [v for pos, v in euros if "kaufpreis" in flat[max(0, pos - 20):pos].lower()]
    if labelled:
        l.price = labelled[0]
    elif euros:
        l.price = max(v for _, v in euros)

    for m in AREA_RE.finditer(flat):
        if "grundst" in flat[max(0, m.start() - 22):m.start()].lower():
            continue
        l.area = de_number(m.group(1))
        break

    m = ROOMS_RE.search(flat)
    if m:
        l.rooms = de_number(m.group(1))

    m = PLZ_RE.search(flat)
    if m:
        l.plz, l.city = m.group(1), m.group(2).strip()

    # Kaufpreisfaktor: direkt aus dem Inserat oder aus Jahresmiete berechnet
    m = FACTOR_RE.search(flat)
    if m:
        l.factor = de_number(m.group(1))
    elif l.price:
        m = YEAR_RENT_RE.search(flat)
        if m and de_number(m.group(1)) > 0:
            l.factor = l.price / de_number(m.group(1))
        else:
            m = MONTH_RENT_RE.search(flat)
            if m and de_number(m.group(1)) > 0:
                l.factor = l.price / (de_number(m.group(1)) * 12)
    if l.factor and cfg.get("gewerbe", {}).get("nebenkosten_pct"):
        l.factor *= 1 + cfg["gewerbe"]["nebenkosten_pct"] / 100  # Einstandspreis statt Kaufpreis

    for ln in lines:
        if len(ln) >= 8 and not EURO_RE.fullmatch(ln) and not AREA_RE.fullmatch(ln):
            l.title = ln[:140]
            break

    # Profil/Art/Region: erst ueber den Suchauftragsnamen im Betreff, sonst heuristisch
    subj = l.subject.lower()
    for p in cfg.get("profile", []):
        if p["tag"].lower() in subj:
            l.profile, l.kind, l.region = p["tag"], p["kind"], p["region"]
            break
    if not l.kind:
        l.kind = "gewerbe" if GEWERBE_RE.search(flat) and "wohnung" not in flat.lower() else "wohnen"
    if not l.region and l.plz:
        for region, prefixes in cfg.get("plz_regions", {}).items():
            if l.plz.startswith(tuple(prefixes)):
                l.region = region
                break
    l.ptype = "haus" if HAUS_RE.search(flat) and "wohnung" not in flat.lower() else "wohnung"
    if l.kind == "gewerbe":
        l.ptype = "gewerbe"


def parse_message(msg, cfg: dict) -> list[Listing]:
    subject = str(msg.get("Subject", ""))
    listings = split_listings(_body_with_markers(msg))
    for l in listings:
        l.subject = subject
        extract_fields(l, cfg)
    return listings


# --------------------------------------------------------------------------
# Datenbank, Bewertung
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  key TEXT PRIMARY KEY, site TEXT, ext_id TEXT, url TEXT, title TEXT, profile TEXT,
  kind TEXT, region TEXT, ptype TEXT, plz TEXT, city TEXT,
  price REAL, price_prev REAL, area REAL, rooms REAL, ppsqm REAL, factor REAL,
  zwang INTEGER, first_seen TEXT, last_seen TEXT, reported INTEGER DEFAULT 0
)"""


def open_db(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    return db


def upsert(db: sqlite3.Connection, l: Listing, today: str) -> str:
    """Gibt 'new', 'reduced' oder 'seen' zurueck."""
    row = db.execute("SELECT price FROM listings WHERE key=?", (l.key,)).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO listings (key,site,ext_id,url,title,profile,kind,region,ptype,plz,city,"
            "price,area,rooms,ppsqm,factor,zwang,first_seen,last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (l.key, l.site, l.ext_id, l.url, l.title, l.profile, l.kind, l.region, l.ptype, l.plz,
             l.city, l.price, l.area, l.rooms, l.ppsqm, l.factor, int(l.zwang), today, today),
        )
        return "new"
    old = row["price"]
    reduced = bool(l.price and old and l.price < old * 0.99)
    db.execute(
        "UPDATE listings SET last_seen=?, price=COALESCE(?,price), ppsqm=COALESCE(?,ppsqm), "
        "factor=COALESCE(?,factor), price_prev=CASE WHEN ? THEN ? ELSE price_prev END, "
        "reported=CASE WHEN ? THEN 0 ELSE reported END WHERE key=?",
        (today, l.price, l.ppsqm, l.factor, reduced, old, reduced, l.key),
    )
    return "reduced" if reduced else "seen"


def peer_median(db, row, cfg, today: date):
    """Median EUR/m2 vergleichbarer Inserate: erst PLZ-Gebiet (3 Stellen), dann Region, dann Basiswert."""
    since = (today - timedelta(days=365)).isoformat()
    min_n = cfg.get("deals", {}).get("min_samples", 8)
    base = "SELECT ppsqm FROM listings WHERE kind=? AND ptype=? AND ppsqm IS NOT NULL AND zwang=0 AND key<>? AND last_seen>=?"
    args = [row["kind"], row["ptype"], row["key"], since]
    scopes = []
    if row["plz"]:
        scopes.append((f"PLZ {row['plz'][:3]}xx", base + " AND plz LIKE ?", args + [row["plz"][:3] + "%"]))
    if row["region"]:
        scopes.append((row["region"], base + " AND region=?", args + [row["region"]]))
    for name, q, a in scopes:
        vals = [r[0] for r in db.execute(q, a)]
        if len(vals) >= min_n:
            return statistics.median(vals), len(vals), name
    b = cfg.get("baseline", {}).get(f"{row['region']}.{row['ptype']}")
    if b:
        return float(b), 0, "manueller Basiswert"
    return None, 0, ""


def evaluate(db, row, cfg, today: date) -> dict | None:
    """Gibt ein Report-Dict zurueck oder None, wenn das Inserat nicht aufgenommen wird."""
    if row["zwang"]:
        return None
    out = dict(row)
    out["flags"] = []
    if row["kind"] == "gewerbe":
        g = cfg.get("gewerbe", {})
        if not row["area"] or row["area"] < g.get("min_area", 1000):
            return None
        mf = g.get("max_factor", 10)
        if row["factor"] is not None:
            if row["factor"] > mf:
                return None
            out["flags"].append(f"Faktor {row['factor']:.1f} <= {mf}")
        else:
            if g.get("unknown_factor", "include") == "skip":
                return None
            out["flags"].append("Faktor unbekannt - manuell pruefen")
        return out
    d = cfg.get("deals", {})
    ppsqm = row["ppsqm"]
    if ppsqm is None or not d.get("min_ppsqm", 500) <= ppsqm <= d.get("max_ppsqm", 30000):
        out["flags"].append("Preis/Flaeche nicht auswertbar")
        out["ratio"] = None
        return out
    med, n, scope = peer_median(db, row, cfg, today)
    if med is None:
        out["ratio"] = None
        out["flags"].append("keine Vergleichsbasis")
        return out
    out["ratio"], out["median"], out["scope"], out["n"] = ppsqm / med, med, scope, n
    if out["ratio"] <= 1 - d.get("discount", 0.20):
        out["deal"] = True
    return out


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def _fmt(v, suffix="", nd=0):
    return "-" if v is None else f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".") + suffix


def _line(r) -> str:
    bits = [f"**{r['title'] or r['url']}**", f"{r['plz']} {r['city']}".strip(), r["region"],
            _fmt(r["price"], " EUR"), _fmt(r["area"], " m²", 0)]
    if r["rooms"]:
        bits.append(f"{_fmt(r['rooms'], ' Zi.', 1)}")
    if r["ppsqm"]:
        bits.append(_fmt(r["ppsqm"], " EUR/m²"))
    if r.get("ratio") is not None:
        diff = (r["ratio"] - 1) * 100
        bits.append(f"{diff:+.0f}% zum Median {_fmt(r['median'], ' EUR/m²')} ({r['scope']}, n={r['n']})")
    if r.get("price_prev"):
        bits.append(f"Preissenkung von {_fmt(r['price_prev'], ' EUR')}")
    if r["flags"]:
        bits.append("; ".join(r["flags"]))
    return "- " + " | ".join(b for b in bits if b) + f"\n  {r['url']}"


def render_report(rows: list[dict], stats: dict, today: date, max_listed: int) -> str:
    deals = sorted((r for r in rows if r.get("deal")), key=lambda r: r["ratio"])
    gew = sorted((r for r in rows if r["kind"] == "gewerbe"), key=lambda r: -(r["area"] or 0))
    rest = sorted(
        (r for r in rows if r["kind"] == "wohnen" and not r.get("deal")),
        key=lambda r: (r.get("ratio") is None, r.get("ratio") or 0),
    )
    out = [f"# Immobilien-Report {today.isoformat()}", ""]
    out.append(
        f"Mails ausgewertet: {stats['mails']} | Inserate gelesen: {stats['parsed']} | neu: {stats['new']} | "
        f"Preissenkungen: {stats['reduced']} | Zwangsversteigerung ausgeschlossen: {stats['zwang']} | "
        f"Gewerbe aussortiert (Flaeche/Faktor): {stats['gewerbe_out']}"
    )
    out += ["", f"## Top-Deals Wohnen ({len(deals)})", ""]
    out += [_line(r) for r in deals] or ["_keine_"]
    out += ["", f"## Gewerbe >= 1.000 m² im Faktor-Rahmen ({len(gew)})", ""]
    out += [_line(r) for r in gew] or ["_keine_"]
    out += ["", f"## Weitere neue Wohnangebote ({len(rest)}, max. {max_listed} gelistet)", ""]
    out += [_line(r) for r in rest[:max_listed]] or ["_keine_"]
    if stats["unparsed"]:
        out += ["", f"> Hinweis: {stats['unparsed']} Mail(s) ohne erkennbare Inserate - Mailformat evtl. geaendert, Parser pruefen."]
    return "\n".join(out) + "\n"


def write_csv(rows: list[dict], path: Path) -> None:
    cols = ["kind", "region", "plz", "city", "title", "price", "area", "rooms", "ppsqm", "factor", "ratio", "price_prev", "url"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])


def send_mail(subject: str, body: str, attachment: Path) -> None:
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(attachment.read_bytes(), maintype="text", subtype="csv", filename=attachment.name)
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "465"))) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


# --------------------------------------------------------------------------
# Mailabruf
# --------------------------------------------------------------------------
def fetch_imap(cfg: dict, since_days: int):
    mail = cfg.get("mail", {})
    since = date.today() - timedelta(days=since_days)
    since_s = f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"
    with imaplib.IMAP4_SSL(os.environ["IMAP_HOST"]) as im:
        im.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
        im.select(mail.get("folder", "INBOX"), readonly=True)  # nichts wird veraendert/als gelesen markiert
        ids: set[bytes] = set()
        for sender in mail.get("senders", ["immobilienscout24", "immowelt"]):
            _, data = im.search(None, "SINCE", since_s, "FROM", f'"{sender}"')
            ids.update(data[0].split())
        for i in sorted(ids, key=int):
            _, d = im.fetch(i, "(RFC822)")
            yield email.message_from_bytes(d[0][1], policy=email.policy.default)


def fetch_eml(directory: str):
    for p in sorted(Path(directory).glob("*.eml")):
        yield email.message_from_bytes(p.read_bytes(), policy=email.policy.default)


def run(cfg: dict, messages, db, today: date, dry_run: bool = False):
    stats = dict(mails=0, parsed=0, new=0, reduced=0, zwang=0, gewerbe_out=0, unparsed=0)
    touched: list[str] = []
    for msg in messages:
        stats["mails"] += 1
        found = parse_message(msg, cfg)
        if not found:
            stats["unparsed"] += 1
        for l in found:
            stats["parsed"] += 1
            res = upsert(db, l, today.isoformat())
            if res in ("new", "reduced"):
                stats[res] += 1
                touched.append(l.key)
    rows = []
    for key in dict.fromkeys(touched):
        r = db.execute("SELECT * FROM listings WHERE key=? AND reported=0", (key,)).fetchone()
        if r is None:
            continue
        if r["zwang"]:
            stats["zwang"] += 1
            continue
        ev = evaluate(db, r, cfg, today)
        if ev is None:
            stats["gewerbe_out"] += 1
        else:
            rows.append(ev)
    if not dry_run:
        db.executemany("UPDATE listings SET reported=1 WHERE key=?", [(r["key"],) for r in rows])
        # ausgefilterte Inserate nicht jeden Tag neu pruefen
        db.executemany("UPDATE listings SET reported=1 WHERE key=?", [(k,) for k in touched])
        db.commit()
    return rows, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--db", default="immo.sqlite")
    ap.add_argument("--out", default="out")
    ap.add_argument("--since-days", type=int, default=None, help="Mails der letzten N Tage (Standard: Config)")
    ap.add_argument("--eml-dir", help="statt IMAP: lokale .eml-Dateien auswerten")
    ap.add_argument("--send", action="store_true", help="Report per SMTP verschicken")
    ap.add_argument("--dry-run", action="store_true", help="Nichts als 'berichtet' markieren")
    a = ap.parse_args(argv)

    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    since_days = a.since_days or cfg.get("mail", {}).get("since_days", 3)
    messages = fetch_eml(a.eml_dir) if a.eml_dir else fetch_imap(cfg, since_days)
    db = open_db(a.db)
    today = date.today()
    rows, stats = run(cfg, messages, db, today, a.dry_run)

    report = render_report(rows, stats, today, cfg.get("report", {}).get("max_listed", 40))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{today}.md").write_text(report, encoding="utf-8")
    write_csv(rows, out / f"{today}.csv")
    print(report)
    if a.send:
        send_mail(f"Immobilien-Report {today}", report, out / f"{today}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
