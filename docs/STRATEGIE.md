# Strategie und Risikoregeln

> Keine Anlageberatung. Diese Strategie ist ein **Startpunkt zum Testen**, kein
> erprobtes Handelssystem. Ob sie nach Kosten Geld verdient, ist offen – das muss
> der Backtest und danach mehrere Wochen Paper-Trading zeigen.

## Hypothese

Kreuzt auf 5-Minuten-Kerzen der kurze EMA (9) den langen EMA (21) von unten,
beginnt häufig eine kurze Aufwärtsbewegung. Zwei Filter sollen Fehlsignale
reduzieren:

- **RSI zwischen 50 und 70**: Es gibt Aufwärtsdruck, die Bewegung ist aber
  noch nicht überhitzt.
- **Kurs über dem Tages-VWAP**: Käufer haben an diesem Tag im Schnitt die
  Oberhand (VWAP = volumengewichteter Durchschnittspreis seit Handelsbeginn).

Trade Republic erlaubt keine Leerverkäufe. Der Bot ist deshalb **nur long**:
Kauf-Signale eröffnen eine Position, Verkaufs-Signale schließen sie.

## Ablauf alle 5 Minuten

1. Mo–Fr während der Xetra-Zeit (09:00–17:30) jeweils 20 Sekunden nach
   Kerzenende (10:05:20, 10:10:20, …) die 5-Minuten-Kerzen laden.
2. Nur **abgeschlossene** Kerzen bewerten. Die laufende Kerze wird ignoriert.
3. Für jeden Wert genau einmal pro neuer Kerze entscheiden (kein doppeltes
   Signal nach Neustart).

## Einstieg (Kauf-Signal)

Alle Bedingungen müssen auf der gerade abgeschlossenen Kerze erfüllt sein:

| Bedingung | Standard |
|---|---|
| EMA 9 kreuzt EMA 21 nach oben | `strategy.ema_fast`, `ema_slow` |
| RSI(14) im Korridor | 50 bis 70 |
| Schlusskurs über Tages-VWAP | `use_vwap_filter: true` |
| Kerzenende im Kaufzeitfenster | 09:15 bis 17:00 |
| Keine offene Position im selben Wert | – |
| Weniger als `max_open_positions` offene Positionen | 3 |
| Tagesverlust-Limit nicht erreicht | 150 € |
| Daten nicht älter als `max_delay_minutes` | 20 Min. |
| Kauf-Signale nicht pausiert (`/pause`) | – |
| Chance mindestens 3× so groß wie die Kosten | `min_reward_cost_ratio` |

## Ausstieg (Verkaufs-Signal)

Geprüft wird jede neue Kerze nach dem Einstieg, in dieser Reihenfolge:

1. **Position aus dem Vortag** → sofort glattstellen (Daytrading).
2. **Stop-Loss**: Tief der Kerze ≤ Einstieg − 2 × ATR.
3. **Kursziel**: Hoch der Kerze ≥ Einstieg + 4 × ATR.
4. **EMA 9 kreuzt EMA 21 nach unten** (`exit_on_cross_down`).
5. **Tagesende**: ab 17:20 alle Positionen schließen.

Erreicht eine Kerze Stop und Ziel gleichzeitig, zählt vorsichtshalber der Stop.

Tipp aus jedem Kauf-Signal: Die Stop-Loss-Order **direkt in der App
mitsetzen**. Der Bot prüft nur alle 5 Minuten, und du reagierst manuell –
eine echte Stop-Order beim Broker greift schneller.

## Positionsgröße

```
Risiko je Aktie  = (Einstieg − Stop) + Spread/Slippage für Kauf und Verkauf
Stückzahl        = (risk_per_trade − 2 × Ordergebühr) / Risiko je Aktie
                   begrenzt durch max_position_value und freies Kapital,
                   abgerundet auf ganze Stück
```

Beispiel mit Standardwerten: Kurs 180 €, ATR 0,30 € → Stop 179,40 €, Ziel
181,20 €. Das Risikobudget (50 €) würde 62 Stück erlauben, der maximale
Positionswert (2.500 €) begrenzt auf 13 Stück.

## Kostenmodell

| Posten | Annahme | Einstellung |
|---|---|---|
| Ordergebühr | 1 € pro Order (Kauf + Verkauf = 2 €) | `costs.fee_per_order` |
| Geld-Brief-Spanne | 0,05 % (je Order die Hälfte) | `costs.spread_pct` |
| Slippage | 0,02 % pro Order | `costs.slippage_pct` |

Bei 2.500 € Positionswert kostet ein Round Trip damit rund 4,25 € –
das sind 0,17 % des Einsatzes, **bevor** überhaupt etwas verdient ist.
Genau daran scheitern die meisten Daytrading-Strategien. Prüfe die Annahmen:
Wie groß ist der Spread bei deinen Werten wirklich in der App?

Steuern (Abgeltungsteuer, Verlustverrechnung) sind in keinem Ergebnis enthalten.

## Risikoregeln

- Nur Geld einsetzen, das **nicht** für Altersvorsorge oder Notgroschen gedacht ist.
- Maximal `risk_per_trade` (50 €) Verlust pro Trade, maximal `max_daily_loss`
  (150 €) pro Tag – danach keine Kauf-Signale mehr bis morgen.
- Keine Positionen über Nacht (Kurslücken).
- Keine Käufe auf veralteten Daten.

## So testest du die Strategie

1. **Backtest** auf den letzten ~60 Tagen aller Werte:
   `python -m daytrading_bot backtest`
2. Parameter in `config.yaml` ändern und erneut testen. Achtung vor
   Überanpassung: Was nur mit ganz bestimmten Zahlen funktioniert, ist meist Zufall.
   Aussagekräftig wird es erst ab etwa 100 Trades.
3. **Paper-Trading**: Bot mehrere Wochen laufen lassen, Signale **nicht**
   handeln, aber mit „✅ Gekauft“ bestätigen. Das Trade-Journal
   (`data/state/journal.csv`) zeigt das Ergebnis nach Kosten.
4. Erst wenn Backtest **und** Paper-Trading nach Kosten positiv sind: kleine
   echte Beträge.

**Abbruchkriterien** (Vorschlag): Profit-Faktor nach Kosten unter 1,1 über
mindestens 100 Trades, oder der maximale Drawdown ist größer, als du emotional
aushältst.
