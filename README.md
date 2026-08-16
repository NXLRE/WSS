# MORE Rush

Ein Endless Runner im Subway-Surfers-Stil. Du sammelst MORE-Produkte,
weichst gesundem Obst aus und rennst vor einem Arzt weg, der dich zurück
auf Rohkost setzen will.

Alles steckt in einer einzigen Datei: `index.html`. Keine Abhängigkeiten,
kein Build, kein Server nötig.

## Spielen

Datei im Browser öffnen:

```
open index.html          # macOS
xdg-open index.html      # Linux
start index.html         # Windows
```

Alternativ per lokalem Server, falls der Browser `file://` einschränkt:

```
python3 -m http.server 8000
# dann http://localhost:8000
```

## Steuerung

| Eingabe | Aktion |
| --- | --- |
| `◀` `▶` / `A` `D` / Wischen links-rechts | Spur wechseln |
| `▲` / `W` / Leertaste / Wischen hoch | Springen |
| `▼` / `S` / Wischen runter | Rutschen |
| `P` oder `Esc` | Pause |
| `M` | Ton an/aus |

## Spielmechanik

**Sammeln.** Fünf MORE-Produkte liegen auf der Strecke — Clear Whey,
Protein Pudding, Fitness Sauce, Protein Riegel, Choco Creme. Jedes Teil
gibt 10 Punkte. Sechs Produkte in Folge erhöhen den Multiplikator, bis
maximal x8. Ein Treffer setzt die Serie zurück.

**Ausweichen.** Die Hindernisse sind ausnahmslos gesund und liegen in drei
lesbaren Varianten vor:

- *Überspringen* — Melonenspalten, Bananenkiste
- *Unterrutschen* — Salat-Schranke, Smoothie-Bar (erkennbar an der
  gestrichelten Bodenlinie)
- *Umfahren* — Brokkolibaum, Apfelturm

Nie sind alle drei Spuren gleichzeitig blockiert, und zwei Reihen halten
immer genug Abstand, damit die Lücke erreichbar bleibt.

**Der Doc.** Er läuft hinter dir und steigt mit jedem Treffer weiter ins
Bild. Drei Treffer und er hat dich. Sauberes Laufen drückt ihn zurück —
aber je schneller es wird, desto zäher hängt er dran.

## Power-ups

**Sahne-Protein** (9 s) — die Arme schwellen auf Bizeps-Format an und
wirken wie ein Magnet: MORE-Produkte im Umkreis fliegen dir zu.

**Wolf-Gesicht** (7,5 s) — Hyperfokus. Fast doppeltes Tempo, doppelte
Punkte, und statt am Obst zu stolpern pflügst du hindurch. Der Doc fällt
beim Aufsammeln ein Stück zurück.

## Technik

- Canvas 2D mit Pseudo-3D-Perspektivprojektion (`FOCAL / z`), keine
  externen Bibliotheken.
- Die Brennweite folgt zwei Bedingungen: der Spieler nimmt rund 30 % der
  Höhe ein, und alle drei Spuren bleiben auch bei maximalem Kameraversatz
  im Bild. Das Minimum gewinnt — dadurch funktionieren Quer- und
  Hochformat mit derselben Logik.
- Figuren und Objekte sind Vektorgrafik in lokalen Welteinheiten, keine
  Bilddateien.
- Sound über WebAudio-Oszillatoren, ebenfalls ohne Assets.
- `prefers-reduced-motion` schaltet Screenshake, Speedlines und
  Bewegungsspuren ab.
- Highscore liegt in `localStorage`.

## Rechtliches

Fan-Parodie ohne kommerziellen Zweck. Kein offizielles Produkt, keine
Verbindung zu einer Marke oder Person. Alle Marken- und Personenbezüge
sind Karikatur.
