# Daytrading-Signal-Bot für Telegram

Der Bot prüft **alle 5 Minuten** die Kurse deutscher Aktien (Xetra) und schickt dir
**Kauf- und Verkaufs-Vorschläge per Telegram**. Die Order führst du selbst in der
Trade-Republic-App aus. Trade Republic hat keine offizielle API, deshalb ist das ein
Signal-Bot und kein Auto-Trader.

> ⚠️ **Keine Anlage- oder Steuerberatung.** Die meisten privaten Daytrading-Bots
> verlieren nach Gebühren und Spreads Geld. Reihenfolge:
> 1. Backtest mit realistischen Kosten,
> 2. mehrere Wochen Paper-Trading (Signale nur beobachten),
> 3. erst dann kleine Beträge. Nur Geld, das nicht für die Altersvorsorge gedacht ist.

## So sieht ein Signal aus

```
🟢 KAUFEN: SAP (SAP.DE)
Kurs: 182,40 € · Kerze 10:30–10:35

Menge: 13 Stück (≈ 2.371,20 €)
Order: Limit-Kauf bis 182,58 €
Stop-Loss: 181,80 € (−0,33 %)
Kursziel: 183,60 € (+0,66 %)
Risiko ≈ 11,93 € · Chance ≈ 11,47 € · Kosten ≈ 4,13 €

Grund: EMA 9 kreuzt EMA 21 nach oben · RSI 58 · Kurs über Tages-VWAP
💡 Stop-Loss-Order bei 181,80 € direkt in der App mitsetzen.
Keine Anlageberatung – Kurs vor der Order in der App prüfen.
        [ ✅ Gekauft ]  [ 🚫 Nicht gekauft ]
```

```
🔴 VERKAUFEN: SAP (SAP.DE)
Grund: Kursziel erreicht (183,60 €)
Kurs: 183,65 € · Kerze 11:00–11:05

Menge: 13 Stück verkaufen
Einstieg: 182,40 € (10:35)
Ergebnis ≈ +12,11 € (+0,51 %) nach Kosten
Keine Anlageberatung – Kurs vor der Order in der App prüfen.
```

(Beispielzahlen zur Veranschaulichung.)

## Was der Bot macht

- **5-Minuten-Raster**: Mo–Fr von 09:00 bis 17:30 jeweils 20 Sekunden nach Kerzenende
  (10:05:20, 10:10:20, …). Bewertet werden nur **abgeschlossene** 5-Minuten-Kerzen.
- **Kauf-Signal**: EMA 9 kreuzt EMA 21 nach oben, RSI zwischen 50 und 70, Kurs über dem
  Tages-VWAP. Dazu Stückzahl, Limit, Stop-Loss und Kursziel. Details in
  [docs/STRATEGIE.md](docs/STRATEGIE.md).
- **Verkaufs-Signal**: Stop-Loss, Kursziel, EMA-Kreuzung nach unten oder 17:20 Uhr
  (Daytrading: keine Positionen über Nacht).
- **Kostenmodell**: 1 € pro Order (Trade Republic, Bestpreis-Ausführung) plus
  angenommener Spread und Slippage. Signale, bei denen die Chance die Kosten nicht
  deutlich übersteigt, werden verworfen.
- **Risikoregeln**: höchstens 50 € Verlust pro Trade, höchstens 3 offene Positionen,
  ab 150 € Tagesverlust keine neuen Käufe, keine Käufe bei veralteten Daten.
- **Merkt sich Positionen**: Nach einem Kauf-Signal nimmt der Bot an, dass du gekauft
  hast, und schickt später das Verkaufs-Signal. Mit **🚫 Nicht gekauft** oder
  `/schliessen` trägst du die Position aus. Der Zustand überlebt Neustarts.
- **Tagesbilanz** um 17:45 und ein **Trade-Journal** (`data/state/journal.csv`).
- Reagiert nur auf **deine** Chat-ID.

## Installation

Voraussetzung: Python 3.10 oder neuer.

```bash
git clone https://github.com/infamousjuka/Day-trading-bot.git
cd Day-trading-bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Telegram einrichten (5 Minuten)

1. In Telegram **@BotFather** öffnen, `/newbot` senden, Namen vergeben. Du bekommst einen
   **Token** wie `123456789:AAH...`.
2. `.env.example` nach `.env` kopieren und den Token eintragen:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAH...
   ```
3. Bot starten: `python -m daytrading_bot run`, dann deinem Bot in Telegram `/start`
   schreiben. Er antwortet mit deiner **Chat-ID**.
4. Chat-ID in `.env` eintragen (`TELEGRAM_CHAT_ID=...`), Bot mit Strg+C beenden und neu starten.
5. Test: `python -m daytrading_bot telegram-test` schickt eine Testnachricht.

Die `.env` wird nicht eingecheckt (`.gitignore`). Ein einmal gepushter Token bleibt in der
Git-Historie. Falls das passiert: Token bei @BotFather mit `/revoke` sperren.

## Befehle in Telegram

| Befehl | Wirkung |
|---|---|
| `/status` | Markt offen/geschlossen, letzte und nächste Prüfung, Tagesergebnis |
| `/kurse` | Letzte Kurse, RSI und Trend aller Werte |
| `/positionen` | Offene Positionen mit geschätztem Ergebnis |
| `/check` | Sofort prüfen (statt auf das nächste Raster zu warten) |
| `/bilanz` | Heutige Bilanz |
| `/pause` / `/weiter` | Kauf-Signale aus/an (Verkaufs-Signale kommen immer) |
| `/schliessen SAP` | Position austragen, ohne Ergebnis (nicht gekauft) |
| `/schliessen SAP 183,50` | Selbst verkauft zu 183,50 € – Ergebnis wird gebucht |
| `/hilfe` | Alle Befehle |

## Kommandozeile

```bash
python -m daytrading_bot run              # Telegram-Bot starten (läuft dauerhaft)
python -m daytrading_bot check            # einmal prüfen, Ausgabe in der Konsole
python -m daytrading_bot telegram-test    # Token und Chat-ID testen
python -m daytrading_bot backtest         # Backtest über die letzten 59 Tage (Yahoo)
python -m daytrading_bot download         # 5-Min-Kerzen nach data/history laden
```

## Konfiguration

Alles Wichtige steht kommentiert in [`config.yaml`](config.yaml):

| Bereich | Beispiele |
|---|---|
| `watchlist` | Welche Aktien (Yahoo-Symbole, `.DE` = Xetra) |
| `schedule` | Raster (`interval_minutes: 5`), Verzögerung nach Kerzenende |
| `market` | Handelszeiten, Kaufzeitfenster, Uhrzeit zum Glattstellen, Feiertage |
| `strategy` | EMA-Längen, RSI-Korridor, VWAP-Filter |
| `risk` | Kapital, Risiko pro Trade, max. Positionswert, Stop/Ziel in ATR, Tageslimit |
| `costs` | Ordergebühr, Spread, Slippage |

Feiertage (Neujahr, Karfreitag, Ostermontag, 1. Mai, 24.–26.12., Silvester) berechnet der
Bot selbst. Sonderfälle kommen in `market.extra_holidays`.

## Backtest

```bash
# Ohne Internet: synthetische Beispieldaten (Zufallspfad, keine echten Kurse!)
python -m daytrading_bot backtest --csv data/sample/SYNTH_5m.csv

# Echte Kurse der Watchlist von Yahoo (5-Min-Kerzen gibt es dort nur ~60 Tage)
python -m daytrading_bot backtest --days 59
python -m daytrading_bot backtest --symbol SAP.DE --symbol SIE.DE

# Mit den Kerzen, die der Bot selbst mitgeschrieben hat (data/history)
python -m daytrading_bot backtest --history

# Eigene CSV (timestamp,open,high,low,close,volume). 1-Min-Kerzen werden zu 5 Min. zusammengefasst
python -m daytrading_bot backtest --csv meine_daten/SAP.DE_1m.csv
```

Der Backtest nutzt **dieselbe Logik wie der Live-Bot**. Annahmen: Ausführung zum
Eröffnungskurs der nächsten Kerze (du handelst manuell), Gebühr + halber Spread +
Slippage pro Order, bei Stop und Ziel in derselben Kerze zählt der Stop. Ergebnisse
landen in `reports/backtest_<datum>/` (`trades.csv`, `equity.csv`, `summary.txt`).
Ergebnisse, die du aufheben willst, kannst du ins Repository einchecken.

Beispielausgabe auf den **synthetischen** Testdaten:

```
Trades:              17 (0,8 pro Tag)
Trefferquote:        29,41 % (5 Gewinner / 12 Verlierer)
Ergebnis vor Kosten: +23,87 €
Kosten gesamt:       71,70 €
Ergebnis netto:      −47,83 € (−0,48 % auf das Kapital, vor Steuern)
```

Typisches Bild: Vor Kosten leicht im Plus, nach Kosten im Minus.

## Daten

- **Live-Kurse** kommen über [yfinance](https://pypi.org/project/yfinance/) von Yahoo
  Finance. Die Schnittstelle ist **inoffiziell**: Yahoo kann sie ändern oder drosseln,
  und Kurse deutscher Börsen können verzögert sein. Jede Nachricht zeigt die Kerzenzeit.
  Sind die Daten älter als 20 Minuten, gibt es keine Kauf-Signale und einen Hinweis.
- **Kein Bid/Ask**: Yahoo liefert nur OHLCV. Der Spread ist deshalb eine Annahme
  (`costs.spread_pct`). Vergleiche ihn mit dem Spread in der App.
- **Trade Republic handelt nicht an Xetra.** Der Preis in der App kann leicht
  vom Signalkurs abweichen. Deshalb gibt es einen Limit-Vorschlag.
- **Eigener Datensatz**: Mit `record_bars: true` schreibt der Bot jede abgeschlossene
  Kerze nach `data/history/<SYMBOL>_5m.csv`. So entsteht über die Zeit mehr Historie,
  als Yahoo hergibt.
- **Andere Datenquelle**: Eine Klasse mit `fetch(symbol, days) -> DataFrame` (Index =
  Kerzenbeginn, Spalten `open, high, low, close, volume`) genügt. Siehe
  `src/daytrading_bot/data/`. Für spätere Vollautomatik braucht es einen Broker mit
  offizieller API (nach meinem Wissen, nicht geprüft: z. B. Interactive Brokers).

## Dauerbetrieb

Der Bot muss während der Handelszeit laufen, z. B. auf einem Raspberry Pi, einem
kleinen Server oder einem Rechner, der nicht schläft.

**Docker:**

```bash
cp .env.example .env    # Token und Chat-ID eintragen
docker compose up -d    # startet automatisch neu
docker compose logs -f
```

**systemd (Linux):** `/etc/systemd/system/daytrading-bot.service`

```ini
[Unit]
Description=Daytrading-Signal-Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/home/pi/Day-trading-bot
ExecStart=/home/pi/Day-trading-bot/.venv/bin/python -m daytrading_bot run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now daytrading-bot
```

Logs stehen in `logs/bot.log`.

## Projektstruktur

```
config.yaml                 Einstellungen (ohne Geheimnisse)
.env.example                Vorlage für Token und Chat-ID
src/daytrading_bot/
  config.py                 config.yaml + .env laden und prüfen
  market_hours.py           Xetra-Handelszeiten, Feiertage, Zeitzone
  data/                     Datenquellen: Yahoo, CSV, Hilfsfunktionen
  indicators.py             EMA, RSI, ATR, Tages-VWAP
  strategy.py               Einstiegs-/Ausstiegsregeln
  risk.py                   Kostenmodell und Positionsgröße
  portfolio.py              Positionen, Zustand (JSON), Trade-Journal
  engine.py                 Signal-Logik (live und Backtest identisch)
  scanner.py                Ein Prüfdurchlauf über die Watchlist
  messages.py               Telegram-Texte auf Deutsch
  telegram_bot.py           Bot, 5-Minuten-Job, Befehle, Knöpfe
  backtest.py               Backtest mit Kosten und Kennzahlen
  cli.py                    Kommandozeile
data/sample/SYNTH_5m.csv    Synthetische Testdaten (keine echten Kurse)
docs/STRATEGIE.md           Hypothese, Regeln, Risiko, Kosten
tests/                      Tests (pytest)
```

## Tests

```bash
pytest
```

Die Tests laufen ohne Internet und ohne Telegram.

## Grenzen

- Die Strategie ist **nicht** auf Profitabilität geprüft. Erst Backtest, dann Paper-Trading.
- Der Bot sieht dein Depot nicht. Positionen stimmen nur, wenn du die Knöpfe bzw.
  `/schliessen` nutzt.
- Zwischen Signal und deiner Order vergehen Minuten. Bei schnellen Bewegungen kann
  der Preis deutlich abweichen.
- Ergebnisse sind Schätzungen nach Gebühren, Spread und Slippage, **vor Steuern**.
- Ob die Yahoo-Daten für deinen Zweck schnell genug sind, prüfst du am besten mit
  `/kurse` während der Handelszeit (Kerzenzeit mit der Uhrzeit vergleichen).
