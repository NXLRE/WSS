# Setup für den Hermes Agent

Ziel: Der Agent legt bei ImmoScout24 und Immowelt **gespeicherte Suchaufträge mit E-Mail-Benachrichtigung** an
und sorgt dafür, dass die Alert-Mails in einem Postfach landen, das `immo_monitor.py` per IMAP lesen kann.
Das Skript selbst greift **nie** auf die Portale zu, es liest nur Mails.

## 1. Postfach
- Eigenes Postfach nur für diesen Zweck (z. B. `immo-alerts@…`), IMAP aktiviert, App-Passwort statt Hauptpasswort.
- Auf beiden Portalen mit dieser Adresse registrieren. Bestätigungsmails, CAPTCHA und 2FA muss ein Mensch erledigen.

## 2. Suchaufträge anlegen (je Portal, also 8 Stück)
**Name exakt wie in der Tabelle** – das Skript ordnet Mails über den Namen im Betreff zu. Falls ein Portal den
Namen nicht in den Betreff schreibt: nach dem ersten Alert prüfen und notfalls je Suchauftrag ein eigenes
Postfach-Label/Filter anlegen. Ohne Namen greift nur der PLZ-Fallback, dann ist die Zuordnung grob.

| Name | Typ | Ort / Umkreis | Filter |
|---|---|---|---|
| `WSS-BER-WOHNEN` | Kaufen · Wohnung **und** Haus (zwei Aufträge, falls das Portal keine Mehrfachauswahl erlaubt) | Berlin, +20 km | kein Preislimit, keine Mindestfläche |
| `WSS-DUS-WOHNEN` | wie oben | Düsseldorf, +20 km | wie oben |
| `WSS-BER-GEWERBE` | Kaufen · Gewerbe (Büro, Halle/Lager, Handel, Gastronomie, Anlageobjekt, Geschäftshaus) | Berlin, +20 km | Fläche ab **1.000 m²** |
| `WSS-DUS-GEWERBE` | wie oben | Düsseldorf, +20 km | Fläche ab **1.000 m²** |

- Benachrichtigung: **sofort** oder täglich per E-Mail, an das Postfach aus Schritt 1.
- Zwangsversteigerungen: Im Portal ausschließen, wenn der Filter existiert (ImmoScout führt sie in einer eigenen
  Rubrik). Das Skript filtert zusätzlich nach Stichworten (Zwangsversteigerung, Amtsgericht, Verkehrswertgutachten …).
- Nach dem Anlegen eine Liste der angelegten Suchaufträge (Portal, Name, Radius, Filter) zurückmelden.

## 3. Skript betreiben
```bash
cp config.example.toml config.toml        # anpassen
export IMAP_HOST=imap.example.com IMAP_USER=immo-alerts@example.com IMAP_PASSWORD=...
export SMTP_HOST=smtp.example.com SMTP_USER=... SMTP_PASSWORD=... MAIL_TO=du@example.com
python3 immo_monitor.py --dry-run          # erster Test, markiert nichts als berichtet
python3 immo_monitor.py --send             # Report als Mail + out/DATUM.md/.csv
```
Täglich, z. B. 07:30 per Cron: `30 7 * * * cd /pfad/immo-monitor && python3 immo_monitor.py --send >> run.log 2>&1`

## 4. Erste Woche: Parser gegen echte Mails prüfen
Das Skript wurde nur mit selbst gebauten Beispielmails getestet. Das tatsächliche Mailformat der Portale kenne ich
nicht und es ändert sich gelegentlich. Der Report meldet „Mails ohne erkennbare Inserate“, wenn nichts geparst wurde.
Dann 2–3 echte Alert-Mails als `.eml` exportieren, in `tests/` als Fixture ablegen und den Parser nachziehen
(`python3 immo_monitor.py --eml-dir ordner --dry-run`).

## Bekannte Grenzen
- Alert-Mails zeigen nur Kerndaten. Jahresmiete/Faktor stehen bei Gewerbe oft nicht drin → „Faktor unbekannt – manuell prüfen“.
- „Deal“ heißt: mindestens 20 % unter dem Median €/m² vergleichbarer, bereits gesehener Inserate (PLZ-Gebiet, dann Region).
  In den ersten Wochen fehlt die Historie („keine Vergleichsbasis“), dann `[baseline]` in der Config mit eigenen Marktwerten füllen.
- Der 20-km-Umkreis kommt aus dem Suchauftrag, das Skript prüft ihn nicht.
