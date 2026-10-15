"""Bounded ephemeral reply snapshots and the AI layer's context contract.

No Discord/SDK dependencies, transcript persistence, or tasks on import. All
memory methods run synchronously on the bot's event loop; snapshots are immutable.
"""

import asyncio
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, replace

from rolfbot.usage import Code, validate_input

MAX_EXCHANGES = 4
MAX_HISTORY_BYTES = 6_000
MAX_REQUEST_TEXT_BYTES = 12 * 1024
MAX_RECORDS = 64
IDLE_SECONDS = 60 * 60
MAX_ASSISTANT_UTF16_UNITS = 1800
MAX_PARTICIPANT_NUMBER = 999_999


@dataclass(frozen=True, slots=True, repr=False)
class Exchange:
    participant: int
    user: str
    assistant: str


@dataclass(frozen=True, slots=True, repr=False)
class Context:
    exchanges: tuple[Exchange, ...] = ()
    participant: int = 1


def participant_text(participant: int, text: str) -> str:
    return f"Participant {participant}:\n{text}"


def history_bytes(exchanges: tuple[Exchange, ...]) -> int:
    return sum(
        len(participant_text(item.participant, item.user).encode("utf-8"))
        + len(item.assistant.encode("utf-8"))
        for item in exchanges
    )


def _valid_participant(value) -> bool:
    return type(value) is int and 1 <= value <= MAX_PARTICIPANT_NUMBER


def _valid_assistant(value) -> bool:
    if type(value) is not str or not value.strip() or len(value) > MAX_ASSISTANT_UTF16_UNITS:
        return False
    try:
        return len(value.encode("utf-16-le")) // 2 <= MAX_ASSISTANT_UTF16_UNITS and not any(
            (unicodedata.category(char) in {"Cs", "Cc"} and char not in "\n\t")
            or unicodedata.bidirectional(char) in {"RLE", "LRE", "RLO", "LRO", "PDF", "LRI", "RLI", "FSI", "PDI"}
            or char in "\u061c\u200e\u200f" for char in value
        )
    except UnicodeError:
        return False


def validate_context(context: Context | None) -> None:
    """Reject malformed/unbounded caller data before iterating or reserving quota."""
    if context is None:
        return
    if (
        type(context) is not Context or type(context.exchanges) is not tuple
        or len(context.exchanges) > MAX_EXCHANGES or not _valid_participant(context.participant)
    ):
        raise ValueError("Invalid bounded conversation context.")
    for item in context.exchanges:
        if (
            type(item) is not Exchange or not _valid_participant(item.participant)
            or type(item.user) is not str or validate_input(item.user).code is not Code.OK
            or not _valid_assistant(item.assistant)
        ):
            raise ValueError("Invalid bounded conversation exchange.")
    if history_bytes(context.exchanges) > MAX_HISTORY_BYTES:
        raise ValueError("Conversation context exceeds its byte limit.")


def request_messages(text: str, context: Context | None, instructions: str) -> list[dict]:
    """Role-safe messages with an independent hard UTF-8 text budget.

    Only complete oldest pairs may be dropped. Current user text is preserved.
    Count rendered participant prefixes as text, including in the history cap.
    """
    if validate_input(text).code is not Code.OK or type(instructions) is not str or len(instructions) > 1000:
        raise ValueError("Invalid bounded request input.")
    validate_context(context)
    current = text if context is None else participant_text(context.participant, text)
    fixed_bytes = len(instructions.encode("utf-8")) + len(current.encode("utf-8"))
    if fixed_bytes > MAX_REQUEST_TEXT_BYTES:
        raise ValueError("Current request exceeds its byte limit.")
    exchanges = () if context is None else context.exchanges
    while exchanges and fixed_bytes + history_bytes(exchanges) > MAX_REQUEST_TEXT_BYTES:
        exchanges = exchanges[1:]
    messages = []
    for item in exchanges:
        messages.extend((
            {"role": "user", "content": participant_text(item.participant, item.user)},
            {"role": "assistant", "content": item.assistant},
        ))
    messages.append({"role": "user", "content": current})
    return messages


@dataclass(frozen=True, slots=True, repr=False)
class Snapshot:
    exchanges: tuple[Exchange, ...] = ()
    # Internal IDs never enter Context or provider messages. Keep only authors
    # whose exchanges remain, and never reuse their labels within this branch.
    participants: tuple[tuple[str, int], ...] = ()
    next_participant: int = 1


@dataclass(frozen=True, slots=True, repr=False)
class Turn:
    context: Context
    participants: tuple[tuple[str, int], ...]
    next_participant: int


def prepare_turn(snapshot: Snapshot | None, author_id: str) -> Turn:
    snapshot = snapshot or Snapshot()
    participants = dict(snapshot.participants)
    label = participants.get(author_id)
    next_label = snapshot.next_participant
    if label is None:
        if next_label > MAX_PARTICIPANT_NUMBER:
            snapshot, participants, next_label = Snapshot(), {}, 1
        label = next_label
        participants[author_id] = label
        next_label += 1
    return Turn(Context(snapshot.exchanges, label), tuple(participants.items()), next_label)


@dataclass(frozen=True, slots=True, repr=False)
class _Record:
    snapshot: Snapshot
    touched: float


class ConversationMemory:
    """At most 64 independent bot-reply snapshots, scoped by guild and channel.

    Replying twice to an older message creates sibling branches. No mutable
    channel-wide transcript, parent pointers, unbounded participant map or index.
    A single expiry timer drops idle text even when no new messages arrive.
    """

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._records: OrderedDict[tuple[int, int, int], _Record] = OrderedDict()
        self._timer = None
        self._closed = False

    def __len__(self):
        self.expire()
        return len(self._records)

    def expire(self) -> None:
        now = self._clock()
        for key, record in list(self._records.items()):
            if now - record.touched >= IDLE_SECONDS:
                del self._records[key]
        self._arm()

    def _arm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._records and not self._closed:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # Synchronous unit tests call expire with a fake clock.
            delay = min(record.touched for record in self._records.values()) + IDLE_SECONDS - self._clock()
            self._timer = loop.call_later(max(0, delay), self.expire)

    def lookup(self, key: tuple[int, int, int]) -> Snapshot | None:
        self.expire()
        record = self._records.get(key)
        return record.snapshot if record is not None else None

    def touch(self, key: tuple[int, int, int]) -> None:
        self.expire()
        if key in self._records:
            self._records[key] = replace(self._records[key], touched=self._clock())
            self._records.move_to_end(key)
            self._arm()

    def remember(self, key: tuple[int, int, int], turn: Turn, user: str, assistant: str) -> None:
        """Call only after successful generation AND confirmed Discord delivery."""
        if self._closed:
            return
        validate_context(turn.context)
        exchange = Exchange(turn.context.participant, user, assistant)
        # Validate the new pair individually before trimming. A valid large pair
        # may exceed 6,000 bytes; omit it entirely rather than truncate UTF-8.
        if validate_input(user).code is not Code.OK or not _valid_assistant(assistant):
            raise ValueError("Invalid completed exchange.")
        exchanges = (*turn.context.exchanges, exchange)
        while len(exchanges) > MAX_EXCHANGES or history_bytes(exchanges) > MAX_HISTORY_BYTES:
            exchanges = exchanges[1:]
        retained = {item.participant for item in exchanges}
        participants = tuple((author, label) for author, label in turn.participants if label in retained)
        snapshot = Snapshot(exchanges, participants, turn.next_participant)
        self.expire()
        if key in self._records:
            return  # A confirmed response ID can never append a duplicate turn.
        while len(self._records) >= MAX_RECORDS:
            self._records.popitem(last=False)
        self._records[key] = _Record(snapshot, self._clock())
        self._arm()

    def close(self) -> None:
        self._closed = True
        self._records.clear()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
