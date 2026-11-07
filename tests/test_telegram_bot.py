import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from conftest import FakeSource, StubStrategy, berlin, make_bars
from daytrading_bot import telegram_bot as tb
from daytrading_bot.config import TelegramConfig
from daytrading_bot.engine import BUY
from daytrading_bot.factory import build

CHAT = 4242


def make_bot(cfg, frames=None, strategy=None, chat_id=CHAT):
    cfg = replace(
        cfg,
        telegram=TelegramConfig(token="123:TEST", chat_id=chat_id),
        risk=replace(cfg.risk, min_reward_cost_ratio=0),
    )
    comps = build(cfg, source=FakeSource(frames or {}))
    if strategy is not None:
        comps.strategy = strategy
        comps.engine.strategy = strategy
        comps.scanner.strategy = strategy
    return tb.TradingBot(comps)


def fake_update(chat_id=CHAT, text="/x"):
    message = MagicMock()
    message.reply_text = AsyncMock()
    message.reply_html = AsyncMock()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        callback_query=None,
    )


def fake_context(args=None):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return SimpleNamespace(args=args or [], bot=bot)


def test_scan_trigger_runs_every_five_minutes(cfg):
    trigger = tb.scan_trigger(cfg, berlin(2026, 1, 1, 0, 0).tzinfo)
    t1 = trigger.get_next_fire_time(None, berlin(2026, 11, 6, 10, 2))
    t2 = trigger.get_next_fire_time(t1, t1 + timedelta(seconds=1))
    assert t1 == berlin(2026, 11, 6, 10, 5, 20)
    assert t2 - t1 == timedelta(minutes=5)
    # Wochenende wird uebersprungen
    assert trigger.get_next_fire_time(None, berlin(2026, 11, 7, 12, 0)) == berlin(2026, 11, 9, 0, 0, 20)


def test_summary_trigger_after_close(cfg):
    trigger = tb.summary_trigger(cfg, berlin(2026, 1, 1, 0, 0).tzinfo)
    assert trigger.get_next_fire_time(None, berlin(2026, 11, 6, 12, 0)) == berlin(2026, 11, 6, 17, 45)


def test_parse_price():
    assert tb.parse_price("185,20") == 185.2
    assert tb.parse_price("1.185,20") == 1185.2
    assert tb.parse_price("185.2") == 185.2
    assert tb.parse_price("abc") is None
    assert tb.parse_price("-3") is None


def test_build_application_registers_handlers(cfg):
    bot = make_bot(cfg)
    app = bot.build_application()
    commands = set()
    for handlers in app.handlers.values():
        for h in handlers:
            commands |= set(getattr(h, "commands", ()))
    assert {"start", "status", "check", "positionen", "schliessen", "pause", "weiter"} <= commands


def test_run_scan_sends_buy_with_buttons_and_saves_state(cfg, monkeypatch):
    df = make_bars([100.0, 100.2], start=berlin(2026, 11, 6, 10, 0))
    bot = make_bot(cfg, {"TEST.DE": df, "OTHER.DE": df}, StubStrategy(entries={1}))
    monkeypatch.setattr(bot.c.calendar, "now", lambda: berlin(2026, 11, 6, 10, 10, 20))
    ctx = fake_context()
    report = asyncio.run(bot.run_scan(ctx.bot))
    assert [s.action for s in report.signals] == [BUY, BUY]
    assert ctx.bot.send_message.await_count == 2
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == CHAT and kwargs["parse_mode"] == "HTML"
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert buttons[0].callback_data.startswith("ok:") and buttons[1].callback_data.startswith("no:")
    assert bot.c.store.path.exists()


def test_all_sources_failing_sends_one_warning(cfg, monkeypatch):
    bot = make_bot(cfg, {})
    monkeypatch.setattr(bot.c.calendar, "now", lambda: berlin(2026, 11, 6, 10, 10, 20))
    ctx = fake_context()
    asyncio.run(bot.run_scan(ctx.bot))
    asyncio.run(bot.run_scan(ctx.bot))
    assert ctx.bot.send_message.await_count == 1
    assert "Keine Kurse abrufbar" in ctx.bot.send_message.await_args.kwargs["text"]


def _query(data, chat_id=CHAT):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = SimpleNamespace(text_html="🟢 <b>KAUFEN</b>")
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), callback_query=query)


def test_buttons_confirm_and_ignore(cfg):
    bot = make_bot(cfg)
    pf = bot.c.portfolio
    pos = pf.open_position("TEST.DE", "Test AG", 5, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    upd = _query(f"ok:{pos.id}")
    asyncio.run(bot.on_button(upd, fake_context()))
    assert pos.confirmed
    assert "Gekauft" in upd.callback_query.edit_message_text.await_args.args[0]

    upd = _query(f"no:{pos.id}")
    asyncio.run(bot.on_button(upd, fake_context()))
    assert "TEST.DE" not in pf.positions

    upd = _query(f"no:{pos.id}")
    asyncio.run(bot.on_button(upd, fake_context()))
    upd.callback_query.answer.assert_awaited_with("Diese Position ist nicht mehr offen.")


def test_foreign_chat_is_ignored(cfg):
    bot = make_bot(cfg)
    pos = bot.c.portfolio.open_position("TEST.DE", "Test AG", 5, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    asyncio.run(bot.on_button(_query(f"no:{pos.id}", chat_id=999), fake_context()))
    assert "TEST.DE" in bot.c.portfolio.positions
    upd = fake_update(chat_id=999)
    asyncio.run(bot._guard(bot.cmd_pause)(upd, fake_context()))
    assert not bot.c.portfolio.paused


def test_close_command(cfg):
    bot = make_bot(cfg)
    pf = bot.c.portfolio
    pf.open_position("TEST.DE", "Test AG", 5, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    upd = fake_update()
    asyncio.run(bot.cmd_close(upd, fake_context(["test", "101,50"])))
    assert not pf.positions
    assert pf.trades[-1].reason_code == "manual" and pf.trades[-1].exit_price == 101.5
    assert bot.cfg.path(bot.cfg.files.journal).exists()

    pf.open_position("TEST.DE", "Test AG", 5, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    asyncio.run(bot.cmd_close(fake_update(), fake_context(["TEST.DE"])))
    assert not pf.positions and len(pf.trades) == 1


def test_start_without_chat_id_reveals_id(cfg):
    bot = make_bot(cfg, chat_id=None)
    upd = fake_update(chat_id=777)
    asyncio.run(bot.cmd_start(upd, fake_context()))
    assert "777" in upd.effective_message.reply_html.await_args.args[0]


@pytest.mark.parametrize("hour", [3, 10, 19])
def test_texts_render(cfg, monkeypatch, hour):
    bot = make_bot(cfg)
    monkeypatch.setattr(bot.c.calendar, "now", lambda: berlin(2026, 11, 6, hour, 0))
    assert "Status" in bot.status_text()
    assert "Raster: 5 Min." in bot.status_text()
    assert "Befehle" in bot.help_text()
    assert "Signal-Bot gestartet" in bot.startup_text()
    assert "Noch keine Kurse" in bot.quotes_text()
    assert "Tagesbilanz" in bot.summary_text(datetime(2026, 11, 6).date())
