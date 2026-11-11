"""Offline review signals for the November false migration incident."""

from collections import deque
from dataclasses import dataclass
import re
import unicodedata


MIGRATION = re.compile(r"\bчат\s+переносится\s+в\s+новую\s+группу\b")
FOLLOW = re.compile(r"\b(?:нажмите|перейдите)\b")
START = re.compile(r"\b(?:запустить|start)\b")
MARKER = re.compile(r"@(?:data_migration_robot|connect_tg_robot)\s+([0-9a-f]{10})")


def migration_notice(text, *, hidden_telegram_link=False):
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return bool(
        MIGRATION.search(normalized)
        and FOLLOW.search(normalized)
        and (hidden_telegram_link or START.search(normalized))
    )


@dataclass(frozen=True)
class Observation:
    at: float
    chat_id: int
    message_id: int
    text: str
    chat_title: str = ""

    @property
    def key(self):
        return self.chat_id, self.message_id


@dataclass
class ActiveWave:
    code: str
    last_at: float
    messages: dict
    processing: bool = False


MAX_WAVE_LINKS = 20


class MigrationWave:
    """Group time-ordered marker observations by campaign code."""

    def __init__(self, window_seconds=15, quiet_seconds=60):
        self.window_seconds = window_seconds
        self.quiet_seconds = quiet_seconds
        self.recent = deque()
        self.last_at = None
        self.active = {}

    def invalidate(self, key):
        self.recent = deque((event, code) for event, code in self.recent if event.key != key)
        for wave in self.active.values():
            wave.messages.pop(key, None)

    def observe(self, event):
        if self.last_at is not None and event.at < self.last_at:
            raise ValueError("Observations must be sorted by time")
        self.last_at = event.at
        while self.recent and self.recent[0][0].at < event.at - self.window_seconds:
            self.recent.popleft()
        self.active = {
            code: wave for code, wave in self.active.items()
            if event.at - wave.last_at <= self.quiet_seconds
        }

        marker = MARKER.fullmatch(event.text.strip().casefold())
        if marker is None:
            return None
        code = marker[1]
        wave = self.active.get(code)
        if wave:
            if event.key in wave.messages:
                return None
            wave.last_at = event.at
            if len(wave.messages) >= MAX_WAVE_LINKS:
                return None
            wave.messages[event.key] = event
            return wave

        if any(
            previous.key == event.key for previous, _ in self.recent
        ):
            return None
        matching = [previous for previous, previous_code in self.recent if previous_code == code]
        self.recent.append((event, code))
        other_chat = next((previous for previous in matching if previous.chat_id != event.chat_id), None)
        if other_chat is None:
            return None
        selected = matching[:MAX_WAVE_LINKS - 1]
        if other_chat not in selected:
            selected[-1] = other_chat
        messages = {previous.key: previous for previous in selected}
        messages[event.key] = event
        wave = ActiveWave(code, event.at, messages)
        self.active[code] = wave
        return wave
