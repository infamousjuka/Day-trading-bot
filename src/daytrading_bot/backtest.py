"""Backtest: dieselbe Signal-Logik wie live, auf historischen Kerzen.

Annahmen (bewusst vorsichtig):
- Ein Signal entsteht am Ende einer Kerze. Du handelst manuell, daher wird zum
  Eroeffnungskurs der *naechsten* Kerze ausgefuehrt.
- Jede Order kostet ``costs.fee_per_order`` plus halben Spread plus Slippage.
- Liegen Stop und Kursziel in derselben Kerze, zaehlt der Stop.
- Keine Steuern, keine Teilausfuehrungen, keine Kursluecken-Sonderfaelle.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .config import Config
from .engine import BUY, SELL, SignalEngine
from .market_hours import MarketCalendar
from .messages import fmt_eur, fmt_num, fmt_pct, fmt_signed_eur
from .portfolio import JOURNAL_FIELDS, ClosedTrade, Portfolio
from .risk import CostModel
from .strategy import EmaCrossStrategy

EXIT_LABELS = {
    "stop": "Stop-Loss",
    "target": "Kursziel",
    "signal": "EMA-Kreuzung abwärts",
    "eod": "Tagesende",
    "overnight": "Vortag",
    "end": "Datenende",
}


@dataclass
class BacktestResult:
    trades: list[ClosedTrade]
    equity: pd.Series
    stats: dict[str, float | int | str]
    rejected: dict[str, int] = field(default_factory=dict)


def run_backtest(cfg: Config, data: dict[str, pd.DataFrame]) -> BacktestResult:
    calendar = MarketCalendar(cfg.market)
    strategy = EmaCrossStrategy(cfg.strategy)
    costs = CostModel.from_config(cfg.costs)
    portfolio = Portfolio(capital=cfg.risk.capital)
    engine = SignalEngine(strategy, calendar, portfolio, cfg.risk, costs, cfg.interval_minutes)
    interval = timedelta(minutes=cfg.interval_minutes)

    prepared = {sym: strategy.prepare(df) for sym, df in data.items() if not df.empty}
    if not prepared:
        raise ValueError("Keine Kursdaten für den Backtest")
    instruments = {sym: cfg.instrument(sym) for sym in prepared}
    positions_by_ts = {sym: {ts: i for i, ts in enumerate(df.index)} for sym, df in prepared.items()}
    timeline = sorted(set().union(*(df.index for df in prepared.values())))

    trades: list[ClosedTrade] = []
    pending_entry: dict[str, str] = {}
    pending_exit: dict[str, ClosedTrade] = {}

    for ts in timeline:
        for sym, df in prepared.items():
            i = positions_by_ts[sym].get(ts)
            if i is None:
                continue
            open_price = float(df["open"].iat[i])
            # Ausfuehrung der Signale aus der Vorkerze zum Eroeffnungskurs
            if sym in pending_exit:
                portfolio.reprice_trade(pending_exit.pop(sym), open_price, costs)
            if sym in pending_entry:
                pid = pending_entry.pop(sym)
                pos = portfolio.positions.get(sym)
                if pos is not None and pos.id == pid:
                    pos.entry_price = open_price
            for sig in engine.on_bar(instruments[sym], df, i):
                if sig.action == BUY:
                    pending_entry[sym] = sig.position_id
                elif sig.action == SELL and sig.trade is not None:
                    pending_exit[sym] = sig.trade
                    trades.append(sig.trade)

    # Am Datenende noch offene Positionen zum letzten Kurs schliessen
    for sym in list(portfolio.positions):
        df = prepared[sym]
        end = calendar.local(df.index[-1].to_pydatetime()) + interval
        trade = portfolio.close_position(
            sym, float(df["close"].iat[-1]), end, "Datenende", "end", costs
        )
        trades.append(trade)

    trades.sort(key=lambda t: t.exit_time)
    start = calendar.local(timeline[0].to_pydatetime())
    equity = pd.Series(
        [cfg.risk.capital] + [cfg.risk.capital + 0.0] * len(trades),
        index=pd.DatetimeIndex([start] + [t.exit_time for t in trades]),
        name="equity",
        dtype=float,
    )
    running = cfg.risk.capital
    for k, trade in enumerate(trades, start=1):
        running += trade.pnl_net
        equity.iat[k] = running

    stats = compute_stats(cfg, trades, equity, prepared)
    return BacktestResult(trades=trades, equity=equity, stats=stats, rejected=dict(engine.rejections))


def compute_stats(
    cfg: Config,
    trades: list[ClosedTrade],
    equity: pd.Series,
    prepared: dict[str, pd.DataFrame],
) -> dict[str, float | int | str]:
    pnl = [t.pnl_net for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    costs = sum(t.costs for t in trades)
    peak = equity.cummax()
    drawdown = equity - peak
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0
    dd_idx = drawdown.idxmin() if len(drawdown) else None
    max_dd_pct = 100.0 * max_dd / float(peak.loc[dd_idx]) if dd_idx is not None and max_dd else 0.0
    days = sorted({d for df in prepared.values() for d in df.index.date})
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.reason_code] = by_reason.get(t.reason_code, 0) + 1
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    stats: dict[str, float | int | str] = {
        "symbols": ", ".join(prepared),
        "start": str(days[0]) if days else "",
        "end": str(days[-1]) if days else "",
        "trading_days": len(days),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100.0 * len(wins) / len(trades) if trades else 0.0,
        "net_pnl": sum(pnl),
        "gross_pnl": sum(pnl) + costs,
        "costs": costs,
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf") if wins else 0.0,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "return_pct": 100.0 * sum(pnl) / cfg.risk.capital,
        "trades_per_day": len(trades) / len(days) if days else 0.0,
    }
    for code, count in by_reason.items():
        stats[f"exit_{code}"] = count
    return stats


def format_stats(result: BacktestResult) -> str:
    s = result.stats
    pf = s["profit_factor"]
    pf_text = "∞" if pf == float("inf") else fmt_num(float(pf), 2)
    lines = [
        "BACKTEST-ERGEBNIS",
        "=" * 50,
        f"Werte:               {s['symbols']}",
        f"Zeitraum:            {s['start']} bis {s['end']} ({s['trading_days']} Handelstage)",
        f"Trades:              {s['trades']} ({fmt_num(float(s['trades_per_day']), 1)} pro Tag)",
        f"Trefferquote:        {fmt_pct(float(s['win_rate_pct']), signed=False)} "
        f"({s['wins']} Gewinner / {s['losses']} Verlierer)",
        f"Ergebnis vor Kosten: {fmt_signed_eur(float(s['gross_pnl']))}",
        f"Kosten gesamt:       {fmt_eur(float(s['costs']))}",
        f"Ergebnis netto:      {fmt_signed_eur(float(s['net_pnl']))} "
        f"({fmt_pct(float(s['return_pct']))} auf das Kapital, vor Steuern)",
        f"Ø Gewinn / Verlust:  {fmt_signed_eur(float(s['avg_win']))} / "
        f"{fmt_signed_eur(float(s['avg_loss']))}",
        f"Profit-Faktor:       {pf_text}",
        f"Max. Drawdown:       {fmt_signed_eur(float(s['max_drawdown']))} "
        f"({fmt_pct(float(s['max_drawdown_pct']))})",
    ]
    exits = [(k[5:], v) for k, v in s.items() if k.startswith("exit_")]
    if exits:
        text = ", ".join(f"{EXIT_LABELS.get(k, k)} {v}" for k, v in sorted(exits, key=lambda x: -int(x[1])))
        lines.append(f"Ausstiege:           {text}")
    if result.rejected:
        text = "; ".join(f"{k}: {v}" for k, v in result.rejected.items())
        lines.append(f"Verworfene Signale:  {sum(result.rejected.values())} ({text})")
    lines.append("=" * 50)
    lines.append(
        "Hinweis: Vergangene Ergebnisse sind keine Garantie. Spread und Slippage sind Annahmen."
    )
    return "\n".join(lines)


def write_report(result: BacktestResult, directory: str | Path) -> Path:
    """trades.csv, equity.csv und summary.txt in einen Ordner schreiben."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=JOURNAL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for trade in result.trades:
            row = trade.to_dict()
            row["entry_price"] = round(trade.entry_price, 4)
            row["exit_price"] = round(trade.exit_price, 4)
            row["costs"] = round(trade.costs, 2)
            row["pnl_net"] = round(trade.pnl_net, 2)
            writer.writerow(row)
    eq = result.equity.copy()
    eq.index.name = "time"
    eq.round(2).to_csv(out / "equity.csv", date_format="%Y-%m-%dT%H:%M:%S%z")
    (out / "summary.txt").write_text(format_stats(result) + "\n", encoding="utf-8")
    return out
