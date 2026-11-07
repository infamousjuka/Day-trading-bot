"""Telegram-Nachrichten (HTML) auf Deutsch formatieren."""

from __future__ import annotations

import html
import re
from datetime import date, datetime

from .engine import BUY, Signal
from .portfolio import ClosedTrade, Portfolio
from .risk import CostModel

DISCLAIMER = "Keine Anlageberatung – Kurs vor der Order in der App prüfen."
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# --------------------------------------------------------------------------- #
# Zahlen im deutschen Format
# --------------------------------------------------------------------------- #


def fmt_num(value: float, decimals: int = 2) -> str:
    text = f"{value:,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def price_decimals(price: float) -> int:
    if abs(price) >= 10:
        return 2
    if abs(price) >= 1:
        return 3
    return 4


def fmt_price(value: float) -> str:
    return f"{fmt_num(value, price_decimals(value))} €"


def fmt_eur(value: float) -> str:
    return f"{fmt_num(value, 2)} €"


def fmt_signed_eur(value: float) -> str:
    if round(value, 2) == 0:
        return "±0,00 €"
    sign = "+" if value > 0 else "−"
    return f"{sign}{fmt_num(abs(value), 2)} €"


def fmt_pct(value: float, signed: bool = True) -> str:
    if not signed:
        return f"{fmt_num(value, 2)} %"
    if round(value, 2) == 0:
        return "±0,00 %"
    sign = "+" if value > 0 else "−"
    return f"{sign}{fmt_num(abs(value), 2)} %"


def fmt_qty(qty: float) -> str:
    if float(qty).is_integer():
        return f"{int(qty)}"
    return fmt_num(qty, 3)


def fmt_time(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def fmt_day(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day.strftime('%d.%m.%Y')}"


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def html_to_text(text: str) -> str:
    """HTML-Nachricht fuer die Konsole in reinen Text umwandeln."""
    return html.unescape(re.sub(r"</?(b|i|u|s|code|pre)>", "", text))


# --------------------------------------------------------------------------- #
# Signale
# --------------------------------------------------------------------------- #


def _title(sig: Signal, word: str, icon: str) -> str:
    inst = sig.instrument
    name = esc(inst.name)
    if inst.name != inst.symbol:
        return f"{icon} <b>{word}: {name}</b> ({esc(inst.symbol)})"
    return f"{icon} <b>{word}: {name}</b>"


def _delay_line(sig: Signal, max_delay: float | None) -> list[str]:
    if sig.delay_minutes is None or max_delay is None:
        return []
    if sig.delay_minutes > max_delay:
        return [f"⚠️ Daten {sig.delay_minutes:.0f} Min. alt – Kurs unbedingt in der App prüfen!"]
    return []


def format_buy(sig: Signal, max_delay: float | None = None) -> str:
    plan = sig.plan
    assert plan is not None
    stop_pct = 100.0 * (plan.stop / plan.entry - 1.0)
    tp_pct = 100.0 * (plan.take_profit / plan.entry - 1.0)
    lines = [
        _title(sig, "KAUFEN", "🟢"),
        f"Kurs: <b>{fmt_price(sig.price)}</b> · Kerze {fmt_time(sig.bar_start)}–{fmt_time(sig.bar_end)}",
        *_delay_line(sig, max_delay),
        "",
        f"Menge: <b>{fmt_qty(plan.qty)} Stück</b> (≈ {fmt_eur(plan.position_value)})",
        f"Order: Limit-Kauf bis <b>{fmt_price(plan.limit)}</b>",
        f"Stop-Loss: <b>{fmt_price(plan.stop)}</b> ({fmt_pct(stop_pct)})",
        f"Kursziel: <b>{fmt_price(plan.take_profit)}</b> ({fmt_pct(tp_pct)})",
        f"Risiko ≈ {fmt_eur(plan.risk_eur)} · Chance ≈ {fmt_eur(plan.reward_eur)} · "
        f"Kosten ≈ {fmt_eur(plan.costs_eur)}",
        "",
        f"Grund: {esc(sig.reason)}",
        f"💡 Stop-Loss-Order bei {fmt_price(plan.stop)} direkt in der App mitsetzen.",
        f"<i>{DISCLAIMER}</i>",
    ]
    return "\n".join(lines)


def format_sell(sig: Signal, max_delay: float | None = None) -> str:
    trade = sig.trade
    assert trade is not None
    reason = esc(sig.reason)
    if sig.reason_code == "stop" and sig.stop is not None:
        reason += f" ({fmt_price(sig.stop)})"
    elif sig.reason_code == "target" and sig.take_profit is not None:
        reason += f" ({fmt_price(sig.take_profit)})"
    entry_day = ""
    if trade.entry_time.date() != trade.exit_time.date():
        entry_day = f"{trade.entry_time.strftime('%d.%m.')} "
    lines = [
        _title(sig, "VERKAUFEN", "🔴"),
        f"Grund: <b>{reason}</b>",
        f"Kurs: <b>{fmt_price(sig.price)}</b> · Kerze {fmt_time(sig.bar_start)}–{fmt_time(sig.bar_end)}",
        *_delay_line(sig, max_delay),
        "",
        f"Menge: <b>{fmt_qty(trade.qty)} Stück</b> verkaufen",
        f"Einstieg: {fmt_price(trade.entry_price)} ({entry_day}{fmt_time(trade.entry_time)})",
        f"Ergebnis ≈ <b>{fmt_signed_eur(trade.pnl_net)}</b> ({fmt_pct(trade.pnl_pct)}) nach Kosten",
        f"<i>{DISCLAIMER}</i>",
    ]
    return "\n".join(lines)


def format_signal(sig: Signal, max_delay: float | None = None) -> str:
    return format_buy(sig, max_delay) if sig.action == BUY else format_sell(sig, max_delay)


# --------------------------------------------------------------------------- #
# Uebersichten
# --------------------------------------------------------------------------- #


def format_positions(portfolio: Portfolio, costs: CostModel) -> str:
    if not portfolio.positions:
        return "📭 Keine offenen Positionen."
    lines = ["📂 <b>Offene Positionen</b>"]
    total = 0.0
    for pos in portfolio.positions.values():
        status = "✅ gekauft" if pos.confirmed else "angenommen"
        lines.append("")
        lines.append(f"<b>{esc(pos.name)}</b> ({esc(pos.symbol)}) · {fmt_qty(pos.qty)} Stück · {status}")
        lines.append(
            f"Einstieg {fmt_price(pos.entry_price)} um {fmt_time(pos.entry_time)} · "
            f"Stop {fmt_price(pos.stop)} · Ziel {fmt_price(pos.take_profit)}"
        )
        last = portfolio.last_price.get(pos.symbol)
        if last is not None:
            price, stamp = last
            pnl = costs.net_pnl(pos.entry_price, price, pos.qty)
            total += pnl
            when = datetime.fromisoformat(stamp)
            lines.append(
                f"Zuletzt {fmt_price(price)} ({fmt_time(when)}) · "
                f"≈ {fmt_signed_eur(pnl)} nach Kosten"
            )
    if len(portfolio.positions) > 1:
        lines.append("")
        lines.append(f"Summe ≈ <b>{fmt_signed_eur(total)}</b> nach Kosten")
    lines.append("")
    lines.append("Austragen: /schliessen SYMBOL")
    return "\n".join(lines)


def format_trades_summary(trades: list[ClosedTrade], day: date, realized: float) -> str:
    lines = [f"📊 <b>Tagesbilanz {fmt_day(day)}</b>"]
    if not trades:
        lines.append("Heute keine abgeschlossenen Signal-Trades.")
    else:
        wins = sum(1 for t in trades if t.pnl_net > 0)
        lines.append(f"Trades: {len(trades)} · Gewinner: {wins} · Verlierer: {len(trades) - wins}")
        lines.append("")
        for t in trades:
            icon = "🟢" if t.pnl_net > 0 else "🔴"
            lines.append(
                f"{icon} {esc(t.name)}: {fmt_time(t.entry_time)}→{fmt_time(t.exit_time)} "
                f"{fmt_signed_eur(t.pnl_net)}"
            )
    lines.append("")
    lines.append(f"Ergebnis (geschätzt, nach Kosten, vor Steuern): <b>{fmt_signed_eur(realized)}</b>")
    return "\n".join(lines)
