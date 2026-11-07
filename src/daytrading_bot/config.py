"""Konfiguration laden: config.yaml (Strategie, Risiko, Kosten) und .env (Geheimnisse)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from datetime import date, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv

SUPPORTED_INTERVALS = (1, 2, 5, 15, 30, 60)


class ConfigError(ValueError):
    """Fehler in der Konfiguration."""


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str


@dataclass(frozen=True)
class ScheduleConfig:
    interval_minutes: int = 5
    delay_seconds: int = 20
    daily_summary: bool = True


@dataclass(frozen=True)
class DataConfig:
    source: str = "yahoo"
    history_days: int = 5
    max_delay_minutes: int = 20
    record_bars: bool = True
    csv_dir: str = "data/history"


@dataclass(frozen=True)
class MarketConfig:
    timezone: str = "Europe/Berlin"
    open: time = time(9, 0)
    close: time = time(17, 30)
    first_entry: time = time(9, 15)
    last_entry: time = time(17, 0)
    flat_at: time | None = time(17, 20)
    extra_holidays: tuple[date, ...] = ()


@dataclass(frozen=True)
class StrategyConfig:
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_min: float = 50.0
    rsi_max: float = 70.0
    atr_period: int = 14
    use_vwap_filter: bool = True
    exit_on_cross_down: bool = True


@dataclass(frozen=True)
class RiskConfig:
    capital: float = 10_000.0
    risk_per_trade: float = 50.0
    max_position_value: float = 2_500.0
    stop_loss_atr: float = 2.0
    take_profit_atr: float = 4.0
    max_open_positions: int = 3
    max_daily_loss: float = 150.0
    whole_shares: bool = True
    min_reward_cost_ratio: float = 3.0
    limit_buffer_pct: float = 0.10


@dataclass(frozen=True)
class CostConfig:
    fee_per_order: float = 1.0
    spread_pct: float = 0.05
    slippage_pct: float = 0.02


@dataclass(frozen=True)
class TelegramConfig:
    startup_message: bool = True
    buttons: bool = True
    token: str | None = None
    chat_id: int | None = None
    api_base_url: str | None = None


@dataclass(frozen=True)
class FilesConfig:
    state: str = "data/state/state.json"
    journal: str = "data/state/journal.csv"
    log: str = "logs/bot.log"
    reports: str = "reports"


@dataclass(frozen=True)
class Config:
    watchlist: tuple[Instrument, ...]
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    data: DataConfig = field(default_factory=DataConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    base_dir: Path = Path(".")

    @property
    def interval_minutes(self) -> int:
        return self.schedule.interval_minutes

    def path(self, relative: str) -> Path:
        """Pfad aus der Konfiguration relativ zum Projektordner aufloesen."""
        p = Path(relative)
        return p if p.is_absolute() else self.base_dir / p

    def instrument(self, symbol: str) -> Instrument:
        for inst in self.watchlist:
            if inst.symbol.upper() == symbol.upper():
                return inst
        return Instrument(symbol=symbol, name=symbol)


# --------------------------------------------------------------------------- #
# Hilfsfunktionen zum Einlesen
# --------------------------------------------------------------------------- #


def parse_time(value: Any, key: str) -> time:
    """Uhrzeit aus YAML lesen.

    YAML 1.1 liest ``17:30`` ohne Anfuehrungszeichen als Zahl (17*60+30).
    Das wird hier abgefangen, damit beide Schreibweisen funktionieren.
    """
    if isinstance(value, time):
        return value
    if isinstance(value, bool):
        raise ConfigError(f"{key}: ungueltige Uhrzeit {value!r}")
    if isinstance(value, int):
        hours, minutes = divmod(value, 60)
        if 0 <= hours < 24:
            return time(hours, minutes)
    if isinstance(value, str):
        try:
            parts = [int(p) for p in value.strip().split(":")]
            return time(*parts)
        except (ValueError, TypeError):
            pass
    raise ConfigError(f'{key}: ungueltige Uhrzeit {value!r}, erwartet z. B. "17:30"')


def _parse_date(value: Any, key: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"{key}: ungueltiges Datum {value!r}, erwartet z. B. 2026-12-24") from exc


def _section(cls: type, raw: Any, name: str, converters: dict[str, Any] | None = None) -> Any:
    """Abschnitt der YAML-Datei in eine Dataclass umwandeln und Tippfehler melden."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Abschnitt '{name}' muss eine Zuordnung (key: value) sein")
    allowed = {f.name: f for f in fields(cls)}
    converters = converters or {}
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed or key in {"token", "chat_id", "api_base_url"}:
            raise ConfigError(f"Unbekannter Eintrag '{name}.{key}'")
        if key in converters:
            value = converters[key](value, f"{name}.{key}")
        else:
            default = getattr(cls(), key)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ConfigError(f"{name}.{key}: true oder false erwartet")
            elif isinstance(default, int):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ConfigError(f"{name}.{key}: ganze Zahl erwartet")
            elif isinstance(default, float):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConfigError(f"{name}.{key}: Zahl erwartet")
                value = float(value)
            elif isinstance(default, str):
                value = str(value)
        kwargs[key] = value
    return cls(**kwargs)


def _optional_time(value: Any, key: str) -> time | None:
    return None if value is None else parse_time(value, key)


def _holidays(value: Any, key: str) -> tuple[date, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{key}: Liste von Daten erwartet")
    return tuple(_parse_date(v, key) for v in value)


def _watchlist(raw: Any) -> tuple[Instrument, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'watchlist' muss mindestens einen Wert enthalten")
    items: list[Instrument] = []
    for entry in raw:
        if isinstance(entry, str):
            items.append(Instrument(symbol=entry, name=entry))
        elif isinstance(entry, dict) and "symbol" in entry:
            unknown = set(entry) - {"symbol", "name"}
            if unknown:
                raise ConfigError(f"Unbekannte Eintraege in watchlist: {sorted(unknown)}")
            symbol = str(entry["symbol"]).strip()
            items.append(Instrument(symbol=symbol, name=str(entry.get("name") or symbol)))
        else:
            raise ConfigError(f"Ungueltiger watchlist-Eintrag: {entry!r}")
    symbols = [i.symbol.upper() for i in items]
    if len(symbols) != len(set(symbols)):
        raise ConfigError("Ein Symbol steht doppelt in der watchlist")
    return tuple(items)


def _validate(cfg: Config) -> None:
    if cfg.schedule.interval_minutes not in SUPPORTED_INTERVALS:
        raise ConfigError(
            f"schedule.interval_minutes muss einer von {SUPPORTED_INTERVALS} sein"
        )
    if not 0 <= cfg.schedule.delay_seconds < 60 * cfg.schedule.interval_minutes:
        raise ConfigError("schedule.delay_seconds muss kleiner als das Raster sein")
    if cfg.data.source not in {"yahoo", "csv"}:
        raise ConfigError("data.source muss 'yahoo' oder 'csv' sein")
    if cfg.data.history_days < 1:
        raise ConfigError("data.history_days muss mindestens 1 sein")
    try:
        ZoneInfo(cfg.market.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unbekannte Zeitzone {cfg.market.timezone!r}") from exc
    m = cfg.market
    if not m.open < m.close:
        raise ConfigError("market.open muss vor market.close liegen")
    if not m.open <= m.first_entry <= m.last_entry <= m.close:
        raise ConfigError("Es muss gelten: open <= first_entry <= last_entry <= close")
    if m.flat_at is not None and not m.last_entry <= m.flat_at <= m.close:
        raise ConfigError("market.flat_at muss zwischen last_entry und close liegen")
    s = cfg.strategy
    if not 1 < s.ema_fast < s.ema_slow:
        raise ConfigError("strategy.ema_fast muss kleiner als strategy.ema_slow sein")
    if not 0 <= s.rsi_min < s.rsi_max <= 100:
        raise ConfigError("strategy.rsi_min/rsi_max: 0 <= rsi_min < rsi_max <= 100")
    if s.rsi_period < 2 or s.atr_period < 2:
        raise ConfigError("strategy.rsi_period und atr_period muessen >= 2 sein")
    r = cfg.risk
    if r.capital <= 0 or r.risk_per_trade <= 0 or r.max_position_value <= 0:
        raise ConfigError("risk.capital, risk_per_trade und max_position_value muessen > 0 sein")
    if r.stop_loss_atr <= 0 or r.take_profit_atr <= 0:
        raise ConfigError("risk.stop_loss_atr und take_profit_atr muessen > 0 sein")
    if r.max_open_positions < 1:
        raise ConfigError("risk.max_open_positions muss mindestens 1 sein")
    if r.max_daily_loss <= 0:
        raise ConfigError("risk.max_daily_loss muss > 0 sein")
    c = cfg.costs
    if c.fee_per_order < 0 or c.spread_pct < 0 or c.slippage_pct < 0:
        raise ConfigError("Kosten duerfen nicht negativ sein")


def load_config(
    path: str | Path = "config.yaml",
    env_file: str | Path | None = ".env",
) -> Config:
    """config.yaml einlesen, .env laden und alles pruefen."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Konfigurationsdatei {path} nicht gefunden")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml muss Abschnitte (key: value) enthalten")

    allowed = {"watchlist", "schedule", "data", "market", "strategy", "risk", "costs", "telegram", "files"}
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"Unbekannte Abschnitte in {path}: {sorted(unknown)}")

    if env_file is not None:
        env_path = Path(env_file)
        if not env_path.is_absolute():
            env_path = path.parent / env_path
        load_dotenv(env_path, override=False)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
    chat_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    try:
        chat_id = int(chat_raw) if chat_raw else None
    except ValueError as exc:
        raise ConfigError("TELEGRAM_CHAT_ID muss eine Zahl sein (z. B. 123456789)") from exc
    api_base_url = os.getenv("TELEGRAM_API_BASE_URL", "").strip() or None

    telegram = _section(TelegramConfig, raw.get("telegram"), "telegram")
    telegram = TelegramConfig(
        startup_message=telegram.startup_message,
        buttons=telegram.buttons,
        token=token,
        chat_id=chat_id,
        api_base_url=api_base_url,
    )

    cfg = Config(
        watchlist=_watchlist(raw.get("watchlist")),
        schedule=_section(ScheduleConfig, raw.get("schedule"), "schedule"),
        data=_section(DataConfig, raw.get("data"), "data"),
        market=_section(
            MarketConfig,
            raw.get("market"),
            "market",
            converters={
                "open": parse_time,
                "close": parse_time,
                "first_entry": parse_time,
                "last_entry": parse_time,
                "flat_at": _optional_time,
                "extra_holidays": _holidays,
            },
        ),
        strategy=_section(StrategyConfig, raw.get("strategy"), "strategy"),
        risk=_section(RiskConfig, raw.get("risk"), "risk"),
        costs=_section(CostConfig, raw.get("costs"), "costs"),
        telegram=telegram,
        files=_section(FilesConfig, raw.get("files"), "files"),
        base_dir=path.resolve().parent,
    )
    _validate(cfg)
    return cfg
