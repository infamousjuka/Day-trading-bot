from datetime import date, time

import pytest

from conftest import ROOT
from daytrading_bot.config import ConfigError, load_config, parse_time


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_API_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


def test_repository_config_loads():
    cfg = load_config(ROOT / "config.yaml", env_file=None)
    assert cfg.interval_minutes == 5
    assert cfg.market.open == time(9, 0)
    assert cfg.market.flat_at == time(17, 20)
    assert [i.symbol for i in cfg.watchlist][0] == "SAP.DE"
    assert cfg.telegram.token is None and cfg.telegram.chat_id is None


def test_parse_time_accepts_yaml_sexagesimal_int():
    # YAML 1.1 macht aus 17:30 (ohne Anfuehrungszeichen) die Zahl 1050
    assert parse_time(1050, "x") == time(17, 30)
    assert parse_time("09:15", "x") == time(9, 15)
    with pytest.raises(ConfigError):
        parse_time("halb zehn", "x")


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_unknown_key_is_reported(tmp_path):
    path = _write(tmp_path, "watchlist: [SAP.DE]\nrisk:\n  risk_per_trd: 10\n")
    with pytest.raises(ConfigError, match="risk.risk_per_trd"):
        load_config(path, env_file=None)


def test_unquoted_times_and_holidays(tmp_path):
    path = _write(
        tmp_path,
        "watchlist: [SAP.DE]\nmarket:\n  open: 9:00\n  close: 17:30\n"
        "  extra_holidays: [2026-06-04]\n",
    )
    cfg = load_config(path, env_file=None)
    assert cfg.market.open == time(9, 0)
    assert cfg.market.close == time(17, 30)
    assert cfg.market.extra_holidays == (date(2026, 6, 4),)


def test_env_values(tmp_path, monkeypatch):
    path = _write(tmp_path, "watchlist: [SAP.DE]\n")
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=-10042\n")
    cfg = load_config(path)
    assert cfg.telegram.token == "123:abc"
    assert cfg.telegram.chat_id == -10042


def test_invalid_chat_id(tmp_path, monkeypatch):
    path = _write(tmp_path, "watchlist: [SAP.DE]\n")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "meine-id")
    with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID"):
        load_config(path, env_file=None)


@pytest.mark.parametrize(
    "snippet, message",
    [
        ("schedule:\n  interval_minutes: 7\n", "interval_minutes"),
        ("strategy:\n  ema_fast: 30\n  ema_slow: 20\n", "ema_fast"),
        ("market:\n  first_entry: '08:00'\n", "first_entry"),
        ("risk:\n  capital: yes\n", "Zahl"),
    ],
)
def test_validation(tmp_path, snippet, message):
    path = _write(tmp_path, "watchlist: [SAP.DE]\n" + snippet)
    with pytest.raises(ConfigError, match=message):
        load_config(path, env_file=None)
