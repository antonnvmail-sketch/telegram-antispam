"""In-process correlation of independently suspicious group messages."""
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from time import monotonic
from unicodedata import normalize
from uuid import uuid4


WINDOW_SECONDS = 60
QUIET_SECONDS = 30
SIMILARITY = 0.90
MAX_RECENT = 2048
MAX_WAVES = 256


def similar(left, right):
    if left == right:
        return True
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    return (matcher.real_quick_ratio() >= SIMILARITY
            and matcher.quick_ratio() >= SIMILARITY
            and matcher.ratio() >= SIMILARITY)


@dataclass
class Candidate:
    message: object
    decision: object
    text: str
    at: float
    selected: bool = False
    wave_id: str | None = None
    deletion: str = "pending"

    @property
    def key(self):
        return self.message.chat.id, self.message.message_id


@dataclass
class SpamWaves:
    clock: object = monotonic
    recent: dict = field(default_factory=dict, init=False)
    active: dict = field(default_factory=dict, init=False)
    identifiers: dict = field(default_factory=dict, init=False)
    edits: dict = field(default_factory=dict, init=False)

    def invalidate(self, message):
        key = message.chat.id, message.message_id
        self.recent.pop(key, None)
        now = self.clock()
        self.edits = {key: at for key, at in self.edits.items() if now - at <= WINDOW_SECONDS}
        self.edits[key] = now
        if len(self.edits) > MAX_RECENT:
            self.edits.pop(next(iter(self.edits)))

    def observe(self, message, decision, text):
        """Return the current match and earlier matching messages to delete."""
        now = self.clock()
        self.recent = {key: item for key, item in self.recent.items()
                       if now - item.at <= WINDOW_SECONDS}
        self.active = {anchor: last for anchor, last in self.active.items()
                       if now - last < QUIET_SECONDS}
        self.identifiers = {anchor: self.identifiers[anchor] for anchor in self.active}
        normalized = " ".join(normalize("NFKC", text).casefold().split())
        item = Candidate(message, decision, normalized, now)
        if item.key in self.recent or now - self.edits.get(item.key, float("-inf")) <= WINDOW_SECONDS:
            return False, []
        for anchor in self.active:
            if similar(anchor, item.text):
                self.active[anchor] = now
                item.selected = True
                item.wave_id = self.identifiers[anchor]
                break

        earlier = []
        if not item.selected:
            matches = [
                previous for previous in self.recent.values()
                if (not previous.selected
                    and abs((message.date - previous.message.date).total_seconds()) <= WINDOW_SECONDS
                    and similar(previous.text, item.text))
            ]
            if any(previous.key[0] != item.key[0] for previous in matches):
                item.selected = True
                self.active[item.text] = now
                item.wave_id = self.identifiers[item.text] = uuid4().hex[:16]
                earlier = matches
                for previous in earlier:
                    previous.selected = True
                    previous.wave_id = item.wave_id

        self.recent[item.key] = item
        if len(self.recent) > MAX_RECENT:
            self.recent.pop(next(iter(self.recent)))
        if len(self.active) > MAX_WAVES:
            oldest = min(self.active, key=self.active.get)
            self.active.pop(oldest)
            self.identifiers.pop(oldest)
        return item.selected, earlier
