# Auftrag an den Hermes Agent: Immobilien-Monitor bauen, betreiben, selbst verbessern

Diese Datei ist vollständig. Du brauchst keine weitere Hilfe von Claude oder einem anderen Modell.
Arbeite die Schritte in Abschnitt 9 der Reihe nach ab. Wenn etwas unklar ist, nimm die sicherere Variante und
frage den Menschen (Abschnitt 11), statt zu raten.

## 1. Ziel
Täglich ein Report mit:
1. **Besonders guten Deals** bei Wohnungen und Häusern (Kauf) in **Berlin + 20 km** und **Düsseldorf + 20 km**.
2. **Allen Gewerbeobjekten (Kauf) ab 1.000 m²** in denselben Gebieten, deren **Einstandspreis höchstens das 10-Fache
   der Jahresnettomiete** beträgt.
3. **Nie** Zwangsversteigerungen, Teilungsversteigerungen oder Objekte mit Verkehrswertgutachten.

Quellen: ImmoScout24 und Immowelt. Der Weg: **gespeicherte Suchaufträge mit E-Mail-Benachrichtigung**.
Du liest die Alert-Mails in einem eigenen Postfach und wertest sie aus.

## 2. Harte Regeln (nie verletzen, nie selbst ändern)
- Du rufst die Portale **nicht** per Skript, Crawler oder Scraper ab. Zugriff auf die Portale nur, um Suchaufträge
  mit E-Mail-Benachrichtigung anzulegen oder zu ändern, wie ein normaler Nutzer. Captcha, 2FA, Bezahlfunktionen,
  Kontaktformulare an Anbieter und Anfragen an Makler übernimmt immer der Mensch.
- Du schreibst **keine** Nachrichten an Anbieter, Makler oder Dritte. Mails gehen nur an die Adresse `MAIL_TO`.
- Zugangsdaten nur aus Umgebungsvariablen. Nie in Dateien, Logs, Reports, Commits oder Chat schreiben.
- Das Mail-Postfach wird **nur lesend** geöffnet (IMAP `readonly`). Nichts löschen, verschieben oder markieren.
- Du änderst diese Datei, Abschnitt 2 und die Tests nicht ohne ausdrückliche Freigabe des Menschen.
  Tests dürfen ergänzt werden, nie gelöscht oder abgeschwächt, um sie grün zu bekommen.
- Zahlen im Report müssen aus dem Inserat stammen oder nachvollziehbar berechnet sein. Nie erfundene Werte.
  Fehlt ein Wert, steht dort „unbekannt“.
- Der Report ist **keine Anlageberatung**. Du bewertest nichts als „sicher“.

## 3. Suchaufträge (einmalig anlegen)
Name **exakt** so, weil die Zuordnung über den Betreff der Alert-Mail läuft:

| Name | Kaufen | Ort | Filter |
|---|---|---|---|
| `WSS-BER-WOHNEN` | Wohnung und Haus (bei Bedarf zwei Aufträge) | Berlin, Umkreis 20 km | kein Preislimit |
| `WSS-DUS-WOHNEN` | Wohnung und Haus | Düsseldorf, Umkreis 20 km | kein Preislimit |
| `WSS-BER-GEWERBE` | Gewerbe (Büro, Halle/Lager, Handel, Gastronomie, Anlageobjekt, Geschäftshaus) | Berlin, 20 km | Fläche ab 1.000 m² |
| `WSS-DUS-GEWERBE` | wie oben | Düsseldorf, 20 km | Fläche ab 1.000 m² |

Auf beiden Portalen. Benachrichtigung: sofort oder täglich per Mail. Zwangsversteigerungen im Portal ausschließen,
falls der Filter existiert. Lege das Ergebnis (Portal, Name, Radius, Filter) in `setup_log.md` ab.
Schreibt das Portal den Suchauftragsnamen nicht in den Betreff, melde das dem Menschen (Abschnitt 11).

## 4. Programm-Aufbau
Python ≥ 3.11, nur Standardbibliothek, Verzeichnis `immo-monitor/`:

```
immo_monitor.py          Hauptprogramm
config.toml              Schwellen und Profile (Beispiel: config.example.toml)
tests/                   Tests + tests/fixtures/*.eml (echte Mails, anonymisiert)
immo.sqlite              Datenbank (nicht in Git)
out/DATUM.md, .csv       Tagesreport
CHANGELOG_AGENT.md       jede Änderung, die du selbst machst: Datum, Grund, Diff-Kurzfassung, Testergebnis
```

Ablauf pro Lauf: (1) Mails der letzten N Tage per IMAP holen → (2) Inserate aus jeder Mail parsen →
(3) in SQLite speichern (Schlüssel `site:ext_id`) → (4) nur **neue** oder **preisgesenkte** Inserate bewerten →
(5) Report schreiben → (6) optional per SMTP an `MAIL_TO`. Zweiter Lauf am selben Tag liefert nichts Neues.

Umgebungsvariablen: `IMAP_HOST, IMAP_USER, IMAP_PASSWORD, SMTP_HOST, SMTP_USER, SMTP_PASSWORD, MAIL_TO`.

## 5. Parsen einer Alert-Mail
- HTML bevorzugen, sonst Text. Jeden Link als Marker behalten.
- Inserat-Links erkennen: `immobilienscout24.de/expose/<Zahl>` und `immowelt.de/expose/<Buchstaben/Ziffern>`.
  Links können Redirects sein, in denen die Expose-URL kodiert steckt: bis zu 3-mal URL-dekodieren, dann suchen.
- Text zwischen einem Inserat-Link und dem nächsten Link gehört zu diesem Inserat. Ein Fremdlink (Abmelden, Footer)
  beendet das Inserat. Mehrere Links zum selben Inserat (Bild, Titel) zusammenführen.
- Felder: Titel (erste sinnvolle Zeile), Kaufpreis (€, deutsches Zahlenformat `1.250.000`, `€/m²`-Angaben ignorieren;
  bei mehreren Beträgen den mit „Kaufpreis“ davor, sonst den größten), Fläche (`m²`, nicht „Grundstück“), Zimmer,
  PLZ + Ort, Kaufpreisfaktor, Jahresnettomiete, monatliche Ist-Miete (×12).
- Art: Wohnung/Haus/Gewerbe aus Suchauftragsname, sonst aus Stichwörtern.
- Zwangsversteigerung-Stichwörter: zwangsversteiger, versteigerung, verkehrswertgutachten, amtsgericht, § 74a,
  aktenzeichen, teilungsversteigerung. Treffer → nie im Report, aber im Zähler.
- Kann ein Feld nicht gelesen werden, bleibt es leer. Rate nie.

## 6. Filter und Bewertung
**Wohnen:** `ppsqm = Preis / Fläche`. Plausibel nur zwischen 500 und 30.000 €/m², sonst „nicht auswertbar“.
Vergleichswert = **Median** von `ppsqm` aller bereits gespeicherten Inserate gleicher Art (Wohnung/Haus) der letzten
365 Tage: zuerst gleiches PLZ-Gebiet (3 Stellen), mind. 8 Inserate; sonst gleiche Region, mind. 8; sonst manueller
Basiswert aus `config.toml`; sonst „keine Vergleichsbasis“. **Deal** = `ppsqm ≤ 80 % × Median`.
Das Inserat selbst zählt nicht in seinen eigenen Median.

**Gewerbe:** nur wenn Fläche ≥ 1.000 m². Faktor = Einstandspreis / Jahresnettomiete, wobei
Einstandspreis = Kaufpreis × (1 + `nebenkosten_pct`/100), Standard 0. Faktor ≤ 10 → aufnehmen.
Faktor > 10 → verwerfen. Faktor nicht ermittelbar → aufnehmen mit Hinweis „Faktor unbekannt – manuell prüfen“.

**Preissenkung:** Preis ≥ 1 % unter dem bisherigen Preis → noch einmal berichten, mit altem Preis.

## 7. Report
Markdown, Reihenfolge: Kopfzeile mit Zählern (Mails, Inserate, neu, Preissenkungen, Zwangsversteigerungen
ausgeschlossen, Gewerbe aussortiert) → **Top-Deals Wohnen** (sortiert nach Abstand zum Median) →
**Gewerbe ≥ 1.000 m²** (nach Fläche) → weitere neue Wohnangebote (max. 40). Je Zeile: Titel, PLZ Ort, Region,
Preis, Fläche, Zimmer, €/m², Abstand zum Median mit Vergleichsbasis (Gebiet, Anzahl), Hinweise, Link.
Zusätzlich CSV mit Semikolon, UTF-8 mit BOM. Hat eine Mail keine erkennbaren Inserate, steht am Ende der Hinweis
„Parser prüfen“.

## 8. Betrieb
Cron täglich ca. 07:30: Lauf mit Versand. Erster Lauf mit `--dry-run` (markiert nichts als berichtet).
Fehler (IMAP-Login, leerer Report an Werktagen, Parserfehler) gehen **sofort** per Mail an `MAIL_TO`, nicht erst
im nächsten Report.

## 9. Bauanleitung in Schritten
Nach jedem Schritt Tests laufen lassen und Ergebnis kurz in `CHANGELOG_AGENT.md` notieren.
1. Verzeichnis, `config.toml`, leere Tests anlegen.
2. Parser (Abschnitt 5) mit Testmails **zuerst**: 1 Wohnung, 1 Gewerbe mit Jahresmiete, 1 Zwangsversteigerung,
   1 Redirect-Link, 1 reine Text-Mail, 1 Mail ohne Inserate. Danach Parser schreiben, bis alles grün ist.
3. SQLite-Speicher mit Idempotenztest (zweiter Lauf: 0 neu) und Preissenkungstest.
4. Bewertung (Abschnitt 6) mit Test: 10 Historien-Inserate, ein 30 % günstigeres wird Deal; ohne Historie kein Deal.
5. Report und CSV (Abschnitt 7).
6. IMAP-Abruf (nur lesend) und `--eml-dir` für lokale Testmails.
7. Suchaufträge anlegen (Abschnitt 3), `setup_log.md` schreiben.
8. Auf die erste echte Alert-Mail warten. Sie als `.eml` in `tests/fixtures/` speichern (Name, Adresse, Telefon,
   Konto-IDs entfernen), Parser dagegen testen und anpassen.
9. `--dry-run`, Ergebnis dem Menschen zeigen, dann Cron aktivieren.

## 10. Selbstverbesserung (nur nach Abnahme von Schritt 9)
Wöchentlich, und sofort bei Parserfehlern:
1. **Messen:** Anteil der Inserate mit gelesenem Preis, Fläche, PLZ; Anzahl Mails ohne Inserate; Anteil „nicht
   auswertbar“. Werte in `metrics.csv` anhängen (Datum, Zahlen).
2. **Erkennen:** Sinkt ein Anteil um mehr als 10 Prozentpunkte, oder tritt „Mail ohne Inserate“ auf, hat sich
   vermutlich das Mailformat geändert.
3. **Beheben:** Neue echte Mail als Fixture speichern (anonymisiert) → Test schreiben, der **rot** ist →
   Parser minimal ändern → alle Tests grün → Commit mit Beschreibung → Eintrag in `CHANGELOG_AGENT.md`.
4. **Nie** bei roten Tests ausliefern. Wenn nach 3 Versuchen nicht grün: letzte funktionierende Version
   wiederherstellen und den Menschen informieren.
5. **Vorschläge statt Änderungen** bei Bedeutungen: Schwellen (Rabatt 20 %, Faktor 10, Fläche 1.000 m²),
   Regionen, neue Quellen. Du darfst Vorschläge mit Belegen machen (z. B. „bei 20 % Rabatt 14 Deals pro Woche,
   bei 25 % noch 6“), aber nur der Mensch ändert sie.
6. **Qualitätscheck der Deals:** Wenn der Mensch Deals als „unbrauchbar“ markiert (z. B. Erbbaurecht, Sanierungsfall,
   Teileigentum, vermietet mit Mietbindung), sammle die Muster in `lernliste.md`, schlage zusätzliche
   Stichwörter-Filter vor und baue sie nach Freigabe ein, jeweils mit Test.

## 11. Fragen an den Menschen (schicke sie gebündelt, nicht einzeln)
- Ist „Einstandspreis max. 10-fach“ als Faktor (Kaufpreis ÷ Jahresnettomiete) gemeint? Soll `nebenkosten_pct` gesetzt werden?
- Sollen Gewerbeobjekte ohne bekannte Miete gelistet werden (Standard: ja, mit Hinweis) oder verworfen?
- Rabatt-Schwelle für „Deal“ (Standard 20 %) und Startwerte für €/m² je Region und Art.
- Schreibt das Portal den Suchauftragsnamen in den Betreff? (Ergebnis nach der ersten Mail melden.)
- Captcha, 2FA oder Registrierung nötig: genaue Stelle nennen und warten.

## 12. Abnahme
Fertig, wenn: alle Tests grün; `--dry-run` auf mindestens 3 echten Mails jedes Portals plausible Zahlen liefert
(vom Menschen stichprobenartig gegen das Portal geprüft); zweiter Lauf liefert 0 neue; kein Zwangsversteigerungs-
Inserat im Report; keine Zugangsdaten in Dateien oder Logs; Cron läuft und Fehlermeldungen kommen an.
