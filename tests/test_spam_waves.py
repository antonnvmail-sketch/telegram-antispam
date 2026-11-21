"""Exercise the wave tracker and the real dispatcher without Telegram network."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest

from decisions import decide
from prepare_demo import prepare
from spam_waves import SpamWaves, similar


ROOT = Path(__file__).resolve().parents[1]
DATE = datetime(2026, 11, 21, tzinfo=timezone.utc)
CHAT, OTHER = -100001, -100002
SPAM = "Claim your guaranteed prize by messaging the campaign operator immediately"
HAM = "Claim your guaranteed prize by messaging the campaign operator immediately?"


class TrackerTests(unittest.TestCase):
    def test_threshold(self):
        self.assertTrue(similar("abcdefghij", "abcdefghi!"))
        self.assertFalse(similar("abcdefghij", "abcdefgh!?"))

    def test_window_duplicate_groups_and_quiet_timeout(self):
        now = 0
        tracker = SpamWaves(clock=lambda: now)

        def observe(chat, mid, text=SPAM, posted=None):
            date = DATE + timedelta(seconds=now if posted is None else posted)
            message = SimpleNamespace(chat=SimpleNamespace(id=chat), message_id=mid, date=date)
            matched, earlier = tracker.observe(message, None, text)
            return matched, [candidate.key for candidate in earlier]

        self.assertEqual(observe(CHAT, 1), (False, []))
        self.assertEqual(observe(CHAT, 1), (False, []))
        self.assertEqual(observe(CHAT, 2), (False, []))
        now = 60
        self.assertEqual(observe(OTHER, 1), (True, [(CHAT, 1), (CHAT, 2)]))
        now = 89
        self.assertEqual(observe(CHAT, 3), (True, []))
        now = 119
        self.assertEqual(observe(CHAT, 4), (False, []))
        now = 180
        self.assertEqual(observe(OTHER, 5), (False, []))
        self.assertEqual(observe(CHAT, 6, posted=100), (False, []))


class DispatcherTests(unittest.TestCase):
    def test_dispatcher(self):
        for dry_run in ("false", "true"):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as folder:
                temp = Path(folder)
                prepare(ROOT / "data.example.txt", temp / "dataset.sqlite", temp / "model.joblib")
                env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1",
                           DATASET_PATH=str(temp / "dataset.sqlite"), MODEL_PATH=str(temp / "model.joblib"), LOG_FILE=str(temp / "bot.log"),
                           TELEGRAM_TOKEN="123456789:" + "x" * 35, ADMIN_IDS="7",
                           CHAT_IDS=f"{CHAT},{OTHER}", MODERATOR_CHAT_IDS="{}",
                           DRY_RUN=dry_run, AUTO_DELETE_DIRECT_USDT="false",
                           EXPERIMENTAL_REVIEW_ALERTS="false", SHADOW_MODE_ENABLED="false")
                result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                                        cwd=temp, env=env, capture_output=True, text=True,
                                        encoding="utf-8", timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


async def exercise_dispatcher():
    roles = {}
    attempts = []
    outcomes = {}
    reports = []
    report_edits = []

    def rows(sql, args=()):
        with sqlite3.connect(os.environ["DATASET_PATH"]) as db:
            return db.execute(sql, args).fetchall()

    class OfflineBot(Bot):
        async def get_chat_member(self, chat_id, user_id, **kwargs):
            value = roles.get((chat_id, user_id), "member")
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(status=value)

        async def __call__(self, method, request_timeout=None):
            if method.__api_method__ == "sendMessage":
                if method.text.startswith("Спам-волна"):
                    reports.append(method.text)
                return types.Message(message_id=909, date=DATE,
                                     chat=types.Chat(id=method.chat_id, type="private"), text=method.text)
            if method.__api_method__ == "editMessageText":
                report_edits.append((method.message_id, method.text))
                return True
            assert method.__api_method__ == "deleteMessage", method
            key = (method.chat_id, method.message_id)
            assert rows("SELECT reason FROM moderation_decisions WHERE chat_id=? AND message_id=? AND action='delete'",
                        key) == [("confirmed_spam_wave",)]
            attempts.append(key)
            if outcomes.get(key) == "reject":
                raise TelegramBadRequest(method=method, message="message can't be deleted")
            if outcomes.get(key) == "timeout":
                raise TimeoutError()
            return True

    with patch("aiogram.Bot", OfflineBot):
        ns = runpy.run_path(str(ROOT / "bot_new.py"))
    sys.excepthook = sys.__excepthook__
    handler_globals = ns["filter_message"].__globals__
    handler_globals["model"] = object()

    def classify(model, text, **kwargs):
        label = "ham" if text == HAM else "spam"
        dry_run = kwargs.pop("dry_run")
        return replace(decide(label, .65, text=text, model_version="sha256:" + "a" * 64, **kwargs), dry_run=dry_run)

    handler_globals["classify_and_decide"] = classify
    tracker = ns["spam_waves"]
    now = 0
    tracker.clock = lambda: now
    update_id = 0

    async def feed(chat, mid, sender, text=SPAM, *, edited=False, caption=False, anonymous=False):
        nonlocal update_id
        update_id += 1
        group = types.Chat(id=chat, type="supergroup")
        message = types.Message(
            message_id=mid, date=DATE + timedelta(seconds=now), chat=group,
            from_user=types.User(id=sender, first_name="Tester", is_bot=False),
            sender_chat=group if anonymous else None, **{"caption" if caption else "text": text},
        )
        await ns["dp"].feed_update(ns["bot"], types.Update(
            update_id=update_id, **{"edited_message" if edited else "message": message},
        ))

    if os.environ["DRY_RUN"] == "true":
        await feed(CHAT, 1, 13)
        await feed(OTHER, 1, 14)
        assert tracker.active and not attempts and not reports
        return

    roles.update({(CHAT, 10): "administrator", (CHAT, 11): "creator",
                  (OTHER, 12): TimeoutError()})
    await feed(CHAT, 1, 10)
    await feed(CHAT, 2, 11)
    await feed(CHAT, 3, 13, anonymous=True)
    await feed(CHAT, 4, 13, HAM)
    await feed(OTHER, 5, 12)
    await feed(OTHER, 6, 14)
    assert attempts == [] and not tracker.active
    await feed(OTHER, 6, 14)
    await feed(OTHER, 7, 15)
    assert attempts == [] and not tracker.active
    # An edit and a promotion between observation and deletion both protect the original.
    await feed(OTHER, 7, 15, HAM, edited=True)
    await feed(OTHER, 8, 16)
    roles[(OTHER, 16)] = "administrator"
    now = 60
    outcomes[(OTHER, 6)] = "reject"
    outcomes[(CHAT, 6)] = "timeout"
    await feed(CHAT, 6, 17, SPAM + "!", caption=True)
    live = os.environ["DRY_RUN"] == "false"
    assert sorted(attempts) == (sorted([(CHAT, 6), (OTHER, 6)]) if live else [])
    assert tracker.active
    now = 89
    await feed(CHAT, 9, 18, HAM)
    await feed(CHAT, 10, 18)
    assert attempts.count((CHAT, 10)) == int(live)
    await feed(CHAT, 10, 18)
    assert attempts.count((CHAT, 10)) == int(live)
    await feed(CHAT, 14, 10)
    await feed(CHAT, 15, 11)
    await feed(CHAT, 16, 13, anonymous=True)
    now = 119
    await feed(CHAT, 11, 18)
    assert not tracker.active and (CHAT, 11) not in attempts
    assert len(attempts) == (3 if live else 0)
    if live:
        for chat, mid, action in ((OTHER, 6, "delete_rejected"), (CHAT, 6, "delete_unknown"),
                                  (CHAT, 10, "deleted_spam")):
            assert rows("SELECT 1 FROM logs WHERE chat_id=? AND message_id=? AND action=?",
                        (chat, mid, action)) == [(1,)]
        assert len(reports) == 1
        assert "Обнаружено: 2" in reports[0] or "Обнаружено: 3" in reports[0]
        wave_id = reports[0].split()[1]
        wave_reports = ns["wave_reports"]
        timer = wave_reports.timers.get(wave_id)
        if timer:
            timer.cancel()
        await wave_reports.close(wave_id)
        assert await wave_reports.correct_message(wave_id, CHAT, 10, 7)
        assert await wave_reports.correct_message(wave_id, CHAT, 10, 7)
        assert not await wave_reports.correct_message(wave_id, CHAT, 999, 7)
        report = wave_reports.get(wave_id)
        assert report["messages"][f"{CHAT}:10"]["deletion"] == "deleted"
        assert len(report["corrections"]) == 1
        assert all(mid == 909 for mid, _ in report_edits)
        assert "Завершена" in report_edits[-1][1] and "Исправлений по сообщениям: 1" in report_edits[-1][1]
        from wave_statistics import read_wave_statistics
        export = read_wave_statistics(ns["review_service"].tickets.path, [CHAT])
        wave = export["waves"][0]
        assert wave["wave_id"] == wave_id and wave["group_ids"] == [str(CHAT)]
        assert wave["duration_seconds"] == 29 and wave["message_corrections"] == 1
        assert wave["deleted"] == 1 and wave["retained_deletions"] == 0
    else:
        assert not reports


if __name__ == "__main__":
    asyncio.run(exercise_dispatcher())
