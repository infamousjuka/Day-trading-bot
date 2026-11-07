"""Telegram-Bot: prueft im Raster (Standard 5 Minuten) und schickt Order-Vorschlaege.

Der Bot fuehrt keine Orders aus. Er schickt Kauf- und Verkaufs-Vorschlaege,
die du selbst in der Trade-Republic-App pruefst und ausfuehrst.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .config import Config
from .engine import BUY, Signal
from .factory import Components, build
from .messages import (
    esc,
    fmt_day,
    fmt_price,
    fmt_signed_eur,
    format_positions,
    format_signal,
    format_trades_summary,
)
from .portfolio import append_journal
from .scanner import ScanReport

log = logging.getLogger(__name__)

COMMANDS: list[tuple[str, str]] = [
    ("status", "Markt, Raster, Tagesergebnis"),
    ("kurse", "Letzte Kurse und Indikatoren"),
    ("positionen", "Offene Positionen"),
    ("check", "Sofort prüfen"),
    ("bilanz", "Heutige Bilanz"),
    ("pause", "Keine neuen Kauf-Signale"),
    ("weiter", "Kauf-Signale wieder aktivieren"),
    ("schliessen", "Position austragen, z. B. /schliessen SAP 185,20"),
    ("hilfe", "Alle Befehle"),
]


def scan_trigger(cfg: Config, tz) -> CronTrigger:
    """Mo-Fr alle ``interval_minutes`` Minuten, kurz nach Kerzenende."""
    return CronTrigger(
        day_of_week="mon-fri",
        minute=f"*/{cfg.interval_minutes}",
        second=cfg.schedule.delay_seconds,
        timezone=tz,
    )


def summary_trigger(cfg: Config, tz) -> CronTrigger:
    """Tagesbilanz 15 Minuten nach Handelsschluss."""
    moment = datetime.combine(date(2000, 1, 3), cfg.market.close) + timedelta(minutes=15)
    return CronTrigger(day_of_week="mon-fri", hour=moment.hour, minute=moment.minute, timezone=tz)


def parse_price(text: str) -> float | None:
    """'185,20' oder '185.20' -> 185.2"""
    cleaned = text.strip().replace("€", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


class TradingBot:
    def __init__(self, comps: Components) -> None:
        self.c = comps
        self.cfg = comps.cfg
        self.lock = asyncio.Lock()
        self.last_scan: datetime | None = None
        self.last_report: ScanReport | None = None
        self.last_error_notice: datetime | None = None

    @property
    def chat_id(self) -> int | None:
        return self.cfg.telegram.chat_id

    # ------------------------------------------------------------------ #
    # Aufbau
    # ------------------------------------------------------------------ #
    def build_application(self) -> Application:
        token = self.cfg.telegram.token
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN fehlt – siehe .env.example")
        builder = (
            ApplicationBuilder()
            .token(token)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
        )
        base = self.cfg.telegram.api_base_url
        if base:
            base = base.rstrip("/")
            builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot")
        app = builder.build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        handlers = {
            "hilfe": self.cmd_help,
            "help": self.cmd_help,
            "status": self.cmd_status,
            "kurse": self.cmd_quotes,
            "positionen": self.cmd_positions,
            "check": self.cmd_check,
            "bilanz": self.cmd_summary,
            "pause": self.cmd_pause,
            "weiter": self.cmd_resume,
            "schliessen": self.cmd_close,
        }
        for name, handler in handlers.items():
            app.add_handler(CommandHandler(name, self._guard(handler)))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_error_handler(self.on_error)
        return app

    async def _post_init(self, app: Application) -> None:
        try:
            await app.bot.set_my_commands([BotCommand(n, d) for n, d in COMMANDS])
        except TelegramError as exc:
            log.warning("Befehlsliste nicht gesetzt: %s", exc)
        if self.chat_id is None:
            log.warning(
                "TELEGRAM_CHAT_ID fehlt. Schreibe dem Bot /start, um deine Chat-ID zu erfahren."
            )
            return
        tz = self.c.calendar.tz
        app.job_queue.run_custom(
            self.scan_job,
            job_kwargs={
                "trigger": scan_trigger(self.cfg, tz),
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 60,
            },
            name="scan",
        )
        if self.cfg.schedule.daily_summary:
            app.job_queue.run_custom(
                self.summary_job,
                job_kwargs={"trigger": summary_trigger(self.cfg, tz), "misfire_grace_time": 900},
                name="tagesbilanz",
            )
        log.info(
            "Bot gestartet: %s Werte, Raster %s Min.",
            len(self.cfg.watchlist),
            self.cfg.interval_minutes,
        )
        if self.cfg.telegram.startup_message:
            await self.send(app.bot, self.startup_text())

    async def _post_shutdown(self, app: Application) -> None:
        self.c.store.save(self.c.portfolio)
        log.info("Zustand gespeichert, Bot beendet.")

    # ------------------------------------------------------------------ #
    # Senden
    # ------------------------------------------------------------------ #
    async def send(self, bot, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
        if self.chat_id is None:
            return False
        for attempt in range(4):
            try:
                await bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
                return True
            except RetryAfter as exc:
                wait = exc.retry_after
                seconds = wait.total_seconds() if isinstance(wait, timedelta) else float(wait)
                await asyncio.sleep(seconds + 1)
            except NetworkError as exc:
                log.warning("Senden fehlgeschlagen (Versuch %s): %s", attempt + 1, exc)
                await asyncio.sleep(2 ** attempt)
            except TelegramError as exc:
                log.error("Telegram hat die Nachricht abgelehnt: %s", exc)
                return False
        log.error("Nachricht konnte nicht gesendet werden:\n%s", text)
        return False

    def buy_buttons(self, sig: Signal) -> InlineKeyboardMarkup | None:
        if sig.action != BUY or not self.cfg.telegram.buttons:
            return None
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Gekauft", callback_data=f"ok:{sig.position_id}"),
                    InlineKeyboardButton("🚫 Nicht gekauft", callback_data=f"no:{sig.position_id}"),
                ]
            ]
        )

    # ------------------------------------------------------------------ #
    # Pruefen
    # ------------------------------------------------------------------ #
    async def scan_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = self.c.calendar.now()
        if not self.c.calendar.should_scan(now, grace_minutes=self.cfg.data.max_delay_minutes):
            return
        await self.run_scan(context.bot)

    async def run_scan(self, bot) -> ScanReport:
        async with self.lock:
            data, errors = await asyncio.to_thread(self.c.scanner.fetch_all)
            now = self.c.calendar.now()
            await asyncio.to_thread(self.c.scanner.record, data, now)
            report = self.c.scanner.process(data, now, errors)
            outgoing = self._outgoing(report)
            self.last_scan = now
            self.last_report = report
            self._persist(report)
        log.info(
            "Prüfung %s: %s Werte, %s Signale, %s Fehler",
            now.strftime("%H:%M:%S"),
            report.checked,
            len(report.signals),
            len(report.errors),
        )
        for text, markup in outgoing:
            await self.send(bot, text, markup)
        return report

    def _outgoing(self, report: ScanReport) -> list[tuple[str, InlineKeyboardMarkup | None]]:
        max_delay = float(self.cfg.data.max_delay_minutes)
        out: list[tuple[str, InlineKeyboardMarkup | None]] = [
            (format_signal(sig, max_delay), self.buy_buttons(sig)) for sig in report.signals
        ]
        out.extend((notice, None) for notice in report.notices)

        flags = self.c.portfolio.flags
        day = report.time.date().isoformat()
        for symbol, minutes in report.stale.items():
            key = f"stale:{symbol}:{day}"
            if key in flags:
                continue
            flags[key] = "1"
            name = esc(self.cfg.instrument(symbol).name)
            out.append(
                (
                    f"⚠️ Kurse für {name} sind {minutes:.0f} Min. alt. Kauf-Signale für diesen "
                    "Wert gibt es erst wieder mit aktuellen Daten (Hinweis kommt 1× pro Tag).",
                    None,
                )
            )

        if report.errors and len(report.errors) == len(self.cfg.watchlist):
            now = report.time
            if self.last_error_notice is None or now - self.last_error_notice > timedelta(hours=1):
                self.last_error_notice = now
                first = next(iter(report.errors.values()))
                out.append((f"⚠️ Keine Kurse abrufbar: {esc(first[:300])}", None))
        return out

    def _persist(self, report: ScanReport) -> None:
        try:
            self.c.store.save(self.c.portfolio)
            append_journal(self.cfg.path(self.cfg.files.journal), report.closed_trades)
        except OSError as exc:
            log.error("Zustand/Journal nicht gespeichert: %s", exc)

    async def summary_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        today = self.c.calendar.now().date()
        if not self.c.calendar.is_trading_day(today):
            return
        await self.send(context.bot, self.summary_text(today))

    # ------------------------------------------------------------------ #
    # Texte
    # ------------------------------------------------------------------ #
    def startup_text(self) -> str:
        names = ", ".join(esc(i.name) for i in self.cfg.watchlist)
        now = self.c.calendar.now()
        if self.c.calendar.is_open(now):
            market = "Xetra ist gerade geöffnet."
        else:
            nxt = self.c.calendar.next_open(now)
            market = f"Xetra öffnet {fmt_day(nxt.date())} um {nxt:%H:%M}."
        return (
            "🤖 <b>Signal-Bot gestartet</b>\n"
            f"Raster: {self.cfg.interval_minutes} Minuten · Werte: {names}\n"
            f"{market}\n"
            "Befehle: /hilfe"
        )

    def help_text(self) -> str:
        names = ", ".join(esc(i.name) for i in self.cfg.watchlist)
        lines = [
            "🤖 <b>Daytrading-Signal-Bot</b>",
            f"Ich prüfe alle {self.cfg.interval_minutes} Minuten die Kurse von {names} "
            "und schicke dir Kauf- und Verkaufs-Vorschläge. Die Order führst du selbst "
            "in der Trade-Republic-App aus.",
            "",
            "<b>Befehle</b>",
            *[f"/{name} – {esc(desc)}" for name, desc in COMMANDS],
            "",
            "Unter jedem Kauf-Signal: <b>✅ Gekauft</b> oder <b>🚫 Nicht gekauft</b>. "
            "Ohne Klick nehme ich an, dass du gekauft hast, und melde mich zum Verkauf.",
            "",
            "<i>Keine Anlageberatung. Die meisten Daytrading-Strategien verlieren nach "
            "Kosten Geld – erst Backtest und Paper-Trading, dann kleine Beträge.</i>",
        ]
        return "\n".join(lines)

    def status_text(self) -> str:
        cal = self.c.calendar
        now = cal.now()
        pf = self.c.portfolio
        if cal.is_open(now):
            market = f"🟢 Xetra geöffnet bis {self.cfg.market.close:%H:%M}"
        else:
            nxt = cal.next_open(now)
            market = f"🔴 Xetra geschlossen – öffnet {fmt_day(nxt.date())} {nxt:%H:%M}"
        last = f"{self.last_scan:%H:%M:%S}" if self.last_scan else "noch keine"
        trigger = scan_trigger(self.cfg, cal.tz)
        nxt_scan = trigger.get_next_fire_time(None, now)
        if nxt_scan is None or not cal.should_scan(nxt_scan, self.cfg.data.max_delay_minutes):
            nxt_scan = trigger.get_next_fire_time(None, cal.next_open(now) + timedelta(seconds=1))
        if nxt_scan is None:
            nxt_text = "–"
        elif nxt_scan.date() == now.date():
            nxt_text = f"{nxt_scan:%H:%M:%S}"
        else:
            nxt_text = f"{fmt_day(nxt_scan.date())} {nxt_scan:%H:%M:%S}"
        realized = pf.realized_on(now.date())
        lines = [
            "📈 <b>Status</b>",
            market,
            f"Raster: {self.cfg.interval_minutes} Min. · letzte Prüfung: {last} · nächste: {nxt_text}",
            "Kauf-Signale: " + ("⏸ pausiert (/weiter)" if pf.paused else "▶️ aktiv"),
            f"Offene Positionen: {len(pf.positions)}/{self.cfg.risk.max_open_positions}",
            f"Heute realisiert: {fmt_signed_eur(realized)} "
            f"(Tageslimit −{self.cfg.risk.max_daily_loss:.0f} €)",
            f"Datenquelle: {self.cfg.data.source}",
        ]
        return "\n".join(lines)

    def quotes_text(self) -> str:
        report = self.last_report
        if report is None or not report.snapshots:
            return "Noch keine Kurse geladen. /check prüft sofort."
        lines = [f"💹 <b>Kurse</b> (Prüfung {report.time:%H:%M})"]
        for inst in self.cfg.watchlist:
            snap = report.snapshots.get(inst.symbol)
            if snap is None:
                err = report.errors.get(inst.symbol)
                lines.append(f"{esc(inst.name)}: ⚠️ {esc((err or 'keine Daten')[:80])}")
                continue
            trend = "↗️" if snap["ema_fast"] > snap["ema_slow"] else "↘️"
            rsi_text = "–" if math.isnan(snap["rsi"]) else f"{snap['rsi']:.0f}"
            when = snap["time"]
            when_text = f"{when:%H:%M}" if when.date() == report.time.date() else f"{when:%d.%m. %H:%M}"
            lines.append(
                f"{trend} <b>{esc(inst.name)}</b> {fmt_price(snap['close'])} "
                f"({when_text}) · RSI {rsi_text}"
            )
        lines.append("")
        lines.append("↗️ = kurzer EMA über langem EMA")
        return "\n".join(lines)

    def summary_text(self, day: date) -> str:
        pf = self.c.portfolio
        text = format_trades_summary(pf.trades_on(day), day, pf.realized_on(day))
        if pf.positions:
            names = ", ".join(esc(p.name) for p in pf.positions.values())
            text += f"\n\n⚠️ Noch offen: {names}. Bitte prüfen (/positionen)."
        return text

    # ------------------------------------------------------------------ #
    # Befehle
    # ------------------------------------------------------------------ #
    def _is_owner(self, update: Update) -> bool:
        chat = update.effective_chat
        return self.chat_id is not None and chat is not None and chat.id == self.chat_id

    def _guard(self, handler):
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if self._is_owner(update):
                await handler(update, context)
            elif self.chat_id is None and update.effective_message:
                await update.effective_message.reply_text(
                    "Bitte zuerst TELEGRAM_CHAT_ID in der .env eintragen (/start zeigt die ID)."
                )
            else:
                chat = update.effective_chat
                log.warning("Befehl von fremdem Chat %s ignoriert", chat.id if chat else "?")

        return wrapped

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        if self.chat_id is None:
            await message.reply_html(
                f"👋 Deine Chat-ID ist <code>{chat.id}</code>.\n"
                "Trage sie in der Datei .env als <code>TELEGRAM_CHAT_ID</code> ein und "
                "starte den Bot neu."
            )
            return
        if not self._is_owner(update):
            log.warning("/start von fremdem Chat %s ignoriert", chat.id)
            return
        await message.reply_html(self.help_text())

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_html(self.help_text())

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_html(self.status_text())

    async def cmd_quotes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_html(self.quotes_text())

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_html(format_positions(self.c.portfolio, self.c.costs))

    async def cmd_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_html(self.summary_text(self.c.calendar.now().date()))

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await message.reply_text("🔎 Prüfe jetzt …")
        report = await self.run_scan(context.bot)
        if report.signals:
            text = f"Fertig: {len(report.signals)} Signal(e) gesendet."
        else:
            text = f"Keine neuen Signale ({report.checked}/{len(self.cfg.watchlist)} Werte geprüft)."
        if report.errors:
            text += "\n⚠️ Fehler bei: " + ", ".join(esc(s) for s in report.errors)
        if not self.c.calendar.is_open(report.time):
            text += "\nHinweis: Xetra ist geschlossen – Kauf-Signale gibt es nur zur Handelszeit."
        await message.reply_html(text)

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.c.portfolio.paused = True
        self.c.store.save(self.c.portfolio)
        await update.effective_message.reply_text(
            "⏸ Keine neuen Kauf-Signale. Verkaufs-Signale für offene Positionen kommen "
            "weiterhin. Fortsetzen mit /weiter."
        )

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.c.portfolio.paused = False
        self.c.store.save(self.c.portfolio)
        await update.effective_message.reply_text("▶️ Kauf-Signale sind wieder aktiv.")

    async def cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        pf = self.c.portfolio
        args = context.args or []
        if not args:
            open_names = ", ".join(p.symbol for p in pf.positions.values()) or "keine"
            await message.reply_text(
                "So geht's:\n"
                "/schliessen SAP – Position austragen (nicht gekauft)\n"
                "/schliessen SAP 185,20 – selbst verkauft zu 185,20 € (Ergebnis wird gebucht)\n"
                f"Offen: {open_names}"
            )
            return
        pos = pf.find(args[0])
        if pos is None:
            await message.reply_text(f"Keine offene Position für {args[0]} gefunden. /positionen")
            return
        if len(args) >= 2:
            price = parse_price(args[1])
            if price is None:
                await message.reply_text(f"Preis {args[1]!r} nicht verstanden, z. B. 185,20")
                return
            trade = pf.close_position(
                pos.symbol, price, self.c.calendar.now(), "Manuell verkauft", "manual", self.c.costs
            )
            append_journal(self.cfg.path(self.cfg.files.journal), [trade])
            self.c.store.save(pf)
            await message.reply_html(
                f"✅ {esc(pos.name)} geschlossen zu {fmt_price(price)} · "
                f"Ergebnis ≈ <b>{fmt_signed_eur(trade.pnl_net)}</b> nach Kosten"
            )
            return
        pf.drop_position(pos.symbol)
        self.c.store.save(pf)
        await message.reply_html(
            f"🗑 {esc(pos.name)} ausgetragen (kein Ergebnis gebucht). "
            "Für diesen Wert kommen wieder Kauf-Signale."
        )

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if not self._is_owner(update):
            await query.answer()
            return
        action, _, pid = (query.data or "").partition(":")
        pf = self.c.portfolio
        pos = next((p for p in pf.positions.values() if p.id == pid), None)
        if pos is None:
            await query.answer("Diese Position ist nicht mehr offen.")
            await self._edit_markup(query, None)
            return
        if action == "ok":
            pos.confirmed = True
            note = "✅ <b>Gekauft</b> – ich melde mich zum Verkauf."
            answer = "Als gekauft markiert."
        elif action == "no":
            pf.drop_position(pos.symbol)
            note = "🚫 <b>Nicht gekauft</b> – wird nicht weiter verfolgt."
            answer = "Wird nicht weiter verfolgt."
        else:
            await query.answer()
            return
        self.c.store.save(pf)
        await query.answer(answer)
        message = query.message
        try:
            if message is not None and getattr(message, "text_html", None):
                await query.edit_message_text(
                    f"{message.text_html}\n\n{note}", parse_mode=ParseMode.HTML
                )
            else:
                await self._edit_markup(query, None)
        except BadRequest as exc:
            log.info("Nachricht nicht bearbeitet: %s", exc)

    @staticmethod
    async def _edit_markup(query, markup) -> None:
        try:
            await query.edit_message_reply_markup(reply_markup=markup)
        except BadRequest as exc:
            log.info("Knöpfe nicht entfernt: %s", exc)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Fehler bei der Verarbeitung", exc_info=context.error)


def run_bot(cfg: Config) -> None:
    """Bot starten und laufen lassen, bis er mit Strg+C beendet wird."""
    comps = build(cfg)
    bot = TradingBot(comps)
    app = bot.build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
