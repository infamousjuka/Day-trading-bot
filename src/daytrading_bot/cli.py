"""Kommandozeile: python -m daytrading_bot <befehl>"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load_config

log = logging.getLogger("daytrading_bot")


def setup_logging(cfg: Config | None, verbose: bool = False, to_file: bool = True) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg is not None and to_file:
        log_path = cfg.path(cfg.files.log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # Diese Bibliotheken protokollieren sonst jede Anfrage.
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram.ext.Application", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _symbols(cfg: Config, given: list[str] | None) -> list[str]:
    return given if given else [i.symbol for i in cfg.watchlist]


# --------------------------------------------------------------------------- #
# Befehle
# --------------------------------------------------------------------------- #


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    from .telegram_bot import run_bot

    if not cfg.telegram.token:
        print("TELEGRAM_BOT_TOKEN fehlt. Kopiere .env.example nach .env und trage den Token ein.")
        return 2
    run_bot(cfg)
    return 0


def cmd_check(cfg: Config, args: argparse.Namespace) -> int:
    """Einmal pruefen, Ergebnis in der Konsole – ohne Telegram, ohne zu speichern."""
    from .factory import build
    from .messages import fmt_num, fmt_price, format_signal, html_to_text

    comps = build(cfg)
    # Nur die jeweils letzte Kerze frisch bewerten
    comps.portfolio.last_bar.clear()
    data, errors = comps.scanner.fetch_all()
    now = comps.calendar.now()
    report = comps.scanner.process(data, now, errors)

    market = "geöffnet" if comps.calendar.is_open(now) else "geschlossen"
    print(f"Prüfung {now:%d.%m.%Y %H:%M:%S} · Xetra {market} · Raster {cfg.interval_minutes} Min.")
    print("-" * 70)
    for inst in cfg.watchlist:
        snap = report.snapshots.get(inst.symbol)
        if snap is None:
            print(f"{inst.name:<20} FEHLER: {errors.get(inst.symbol, 'keine Daten')}")
            continue
        trend = "aufwärts" if snap["ema_fast"] > snap["ema_slow"] else "abwärts"
        print(
            f"{inst.name:<20} {fmt_price(snap['close']):>12}  Kerzenende {snap['time']:%d.%m. %H:%M}"
            f"  RSI {fmt_num(snap['rsi'], 1):>5}  EMA-Trend {trend}"
        )
    print("-" * 70)
    if not report.signals:
        print("Keine Signale auf der letzten Kerze.")
    for sig in report.signals:
        print(html_to_text(format_signal(sig, float(cfg.data.max_delay_minutes))))
        print()
    for symbol, why in comps.engine.last_rejection.items():
        print(f"Hinweis {symbol}: Signal verworfen – {why}")
    return 1 if errors and len(errors) == len(cfg.watchlist) else 0


def cmd_telegram_test(cfg: Config, args: argparse.Namespace) -> int:
    """Testnachricht schicken, um Token und Chat-ID zu pruefen."""
    from telegram import Bot
    from telegram.error import TelegramError

    token, chat_id = cfg.telegram.token, cfg.telegram.chat_id
    if not token:
        print("TELEGRAM_BOT_TOKEN fehlt in .env")
        return 2

    async def _run() -> int:
        kwargs = {}
        if cfg.telegram.api_base_url:
            base = cfg.telegram.api_base_url.rstrip("/")
            kwargs = {"base_url": f"{base}/bot", "base_file_url": f"{base}/file/bot"}
        async with Bot(token, **kwargs) as bot:
            me = await bot.get_me()
            print(f"Bot gefunden: @{me.username}")
            if chat_id is None:
                print("TELEGRAM_CHAT_ID fehlt. Starte den Bot (run) und schreibe ihm /start.")
                return 2
            await bot.send_message(chat_id, "✅ Testnachricht vom Daytrading-Signal-Bot.")
            print(f"Testnachricht an Chat {chat_id} gesendet.")
        return 0

    try:
        return asyncio.run(_run())
    except TelegramError as exc:
        print(f"Telegram-Fehler: {exc}")
        return 1


def _load_backtest_data(cfg: Config, args: argparse.Namespace) -> dict:
    from .data import (
        YahooSource,
        bar_spacing_minutes,
        history_path,
        load_csv,
        resample_bars,
    )

    tz = cfg.market.timezone
    data = {}
    if args.csv:
        for raw in args.csv:
            path = Path(raw)
            stem = path.stem
            suffix = f"_{cfg.interval_minutes}m"
            symbol = stem[: -len(suffix)] if stem.endswith(suffix) else stem.rsplit("_", 1)[0]
            data[symbol] = load_csv(path, tz)
    elif args.history:
        directory = cfg.path(cfg.data.csv_dir)
        for symbol in _symbols(cfg, args.symbol):
            data[symbol] = load_csv(history_path(directory, symbol, cfg.interval_minutes), tz)
    else:
        source = YahooSource(cfg.interval_minutes, tz)
        for symbol in _symbols(cfg, args.symbol):
            print(f"Lade {symbol} von Yahoo ({args.days} Tage) …")
            data[symbol] = source.fetch(symbol, args.days)

    for symbol, df in list(data.items()):
        spacing = bar_spacing_minutes(df)
        if spacing is None:
            continue
        if spacing < cfg.interval_minutes:
            print(f"{symbol}: {spacing:g}-Min-Kerzen werden zu {cfg.interval_minutes}-Min-Kerzen zusammengefasst.")
            data[symbol] = resample_bars(df, cfg.interval_minutes)
        elif spacing > cfg.interval_minutes:
            raise ValueError(
                f"{symbol}: Daten haben {spacing:g}-Min-Kerzen, das Raster ist aber "
                f"{cfg.interval_minutes} Min."
            )
    return data


def cmd_backtest(cfg: Config, args: argparse.Namespace) -> int:
    from .backtest import format_stats, run_backtest, write_report

    data = _load_backtest_data(cfg, args)
    result = run_backtest(cfg, data)
    print(format_stats(result))
    out = Path(args.out) if args.out else cfg.path(cfg.files.reports) / (
        f"backtest_{datetime.now():%Y%m%d_%H%M%S}"
    )
    write_report(result, out)
    print(f"Details: {out}/ (trades.csv, equity.csv, summary.txt)")
    return 0


def cmd_download(cfg: Config, args: argparse.Namespace) -> int:
    from .data import YahooSource, history_path, merge_into_csv

    source = YahooSource(cfg.interval_minutes, cfg.market.timezone)
    directory = cfg.path(cfg.data.csv_dir)
    failed = 0
    for symbol in _symbols(cfg, args.symbol):
        try:
            df = source.fetch(symbol, args.days)
        except Exception as exc:
            print(f"{symbol}: Fehler – {exc}")
            failed += 1
            continue
        path = history_path(directory, symbol, cfg.interval_minutes)
        total = merge_into_csv(df, path, cfg.market.timezone)
        print(f"{symbol}: {len(df)} Kerzen geladen, {total} in {path}")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daytrading-bot",
        description="Daytrading-Signal-Bot: Kauf- und Verkaufs-Vorschläge per Telegram.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-c", "--config", default="config.yaml", help="Pfad zur config.yaml")
    parser.add_argument("--env", default=".env", help="Pfad zur .env-Datei")
    parser.add_argument("-v", "--verbose", action="store_true", help="Ausführliche Logs")
    sub = parser.add_subparsers(dest="command", required=True, metavar="BEFEHL")

    sub.add_parser("run", help="Telegram-Bot starten (prüft im Raster, Standard 5 Minuten)")
    sub.add_parser("check", help="Einmal prüfen und in der Konsole ausgeben (ohne Telegram)")
    sub.add_parser("telegram-test", help="Testnachricht an Telegram senden")

    bt = sub.add_parser("backtest", help="Strategie auf historischen Kursen testen")
    bt.add_argument("--csv", action="append", help="CSV-Datei(en) mit Kerzen statt Yahoo")
    bt.add_argument("--symbol", action="append", help="Symbol(e), Standard: watchlist")
    bt.add_argument("--days", type=int, default=59, help="Tage Historie von Yahoo (max. 59 bei 5 Min.)")
    bt.add_argument("--history", action="store_true", help="Mitgeschriebene Kerzen aus data/history nutzen")
    bt.add_argument("--out", help="Ordner für die Ergebnisse")

    dl = sub.add_parser("download", help="Kerzen von Yahoo nach data/history laden")
    dl.add_argument("--symbol", action="append", help="Symbol(e), Standard: watchlist")
    dl.add_argument("--days", type=int, default=59, help="Tage Historie (max. 59 bei 5 Min.)")
    return parser


COMMAND_HANDLERS = {
    "run": cmd_run,
    "check": cmd_check,
    "telegram-test": cmd_telegram_test,
    "backtest": cmd_backtest,
    "download": cmd_download,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config, args.env)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg, args.verbose, to_file=args.command == "run")
    try:
        return COMMAND_HANDLERS[args.command](cfg, args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as exc:
        log.debug("Fehler", exc_info=True)
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
