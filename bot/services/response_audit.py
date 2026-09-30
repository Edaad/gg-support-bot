"""Response audit builder: one row per moment a human support agent was needed.

Deterministic, rule-based pass over the nightly transcript for ET day D plus its
tail (D+1 00:00–03:00 ET). For each support group it finds clock starts, the
first human staff reply after each, and writes ``response_events``. Slow or
unanswered events (``is_candidate``) carry a transcript excerpt so an external
judge can post verdicts through ``/api/response-audit``.

Rules (docs/RESPONSE_AUDIT.md):

* Roles by sender id only: staff (see ``response_audit_staff``), bot
  (``is_bot`` or a known translation / club bot username), the chat's player,
  anything else = unknown sender (treated as a player, flagged).
* Clock starts on day D:
  ``player_question`` (a qualifying player burst), ``bot_handoff`` (the bot's
  "agent / Admin will be with you shortly" line), ``slack_escalation`` (an
  ``escalation_events`` row with a human-needed reason), ``crypto_txid`` (a
  hash-like player message after the bot's TxID prompt).
* Clock stops at the first later message from staff that is not an automated
  staff-account post. Bot messages never stop it.

The rule engine (:func:`build_chat_events`) is pure; loading and persisting
are separate so it can be tested on synthetic transcripts.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bot.services.agent_handoff_copy import AGENT_HANDOFF_LINES
from bot.services.automated_staff_messages import STAFF_ACTION_KINDS
from bot.services.escalation_notification import (
    DEPOSIT_SENT_ACK_COPY_CRYPTO,
    REASON_AUTO_CASHOUT_ESCALATION,
    REASON_DEPOSIT_INCOMPLETE,
    REASON_DEPOSIT_SENT_TIMEOUT,
    REASON_DEPOSIT_SENT_UNBOUND,
    REASON_EARLYRB_AUTO_FAILED,
    REASON_PAYMENT_MANUAL_ACTION,
    REASON_RPA_CASHOUT_FAILED,
    REASON_RPA_CASHOUT_UNCERTAIN,
    REASON_RPA_DEPOSIT_FAILED,
    REASON_RPA_DEPOSIT_UNCERTAIN,
    REASON_TRANSFER_ESCALATION,
    REASON_UNION_DEPOSIT_FIRST,
    REASON_UNION_DEPOSIT_REPEAT,
)
from bot.services.escalation_observability import (
    DECISION_SKIPPED,
    REASON_DEPOSIT_FLOW_ANSWER,
    REASON_EXPECTED_FLOW,
    REASON_FLOW_CMD,
)
from bot.services.group_chat_transcript_fetch import (
    STATUS_COMPLETE,
    et_day_window_utc,
    et_tail_window_utc,
)
from bot.services.support_group_idle_episode import is_player_gratitude_ack

logger = logging.getLogger(__name__)

RULE_VERSION = "ra-2"

START_PLAYER_QUESTION = "player_question"
START_BOT_HANDOFF = "bot_handoff"
START_SLACK_ESCALATION = "slack_escalation"
START_CRYPTO_TXID = "crypto_txid"
START_KINDS = (
    START_PLAYER_QUESTION,
    START_BOT_HANDOFF,
    START_SLACK_ESCALATION,
    START_CRYPTO_TXID,
)

CANDIDATE_THRESHOLD_SECONDS = 300
BURST_GAP_SECONDS = 60
ATTACH_WINDOW_SECONDS = 60
FLOW_WINDOW = timedelta(minutes=10)
TXID_PROMPT_WINDOW = timedelta(minutes=60)
STAFF_WIZARD_WINDOW = timedelta(minutes=15)
SLACK_TRIGGER_LOOKBACK = timedelta(minutes=5)
EXCERPT_BEFORE = timedelta(minutes=20)
EXCERPT_AFTER = timedelta(minutes=5)
EXCERPT_MAX_MESSAGES = 60

# escalation_events reasons that start a clock.
SLACK_START_REASONS = frozenset(
    {
        REASON_RPA_DEPOSIT_FAILED,
        REASON_RPA_CASHOUT_FAILED,
        REASON_RPA_DEPOSIT_UNCERTAIN,
        REASON_RPA_CASHOUT_UNCERTAIN,
        REASON_DEPOSIT_SENT_TIMEOUT,
        REASON_DEPOSIT_SENT_UNBOUND,
        REASON_UNION_DEPOSIT_FIRST,
        REASON_UNION_DEPOSIT_REPEAT,
        REASON_PAYMENT_MANUAL_ACTION,
    }
)
# Never a trigger (ra-2): ``deposit_incomplete`` is the bot's own 10-minute
# "Hey! Just checking in…" deposit reminder, not a human-needed moment. A real
# player message after it still opens its own player_question.
SLACK_IGNORED_REASONS = frozenset({REASON_DEPOSIT_INCOMPLETE})
# Slack reasons the bot can resolve itself (payment received / credits added).
BOT_RESOLVABLE_REASONS = frozenset(
    {REASON_DEPOSIT_SENT_UNBOUND, REASON_DEPOSIT_SENT_TIMEOUT}
)
# Staff message this long before a slack_escalation / bot_handoff = already handled.
STAFF_LOOKBACK_SECONDS = 120
BOT_RESOLVE_WINDOW = timedelta(minutes=5)
# Logged alongside a bot hand-off line; attach to it, never start on their own.
SLACK_ATTACH_ONLY_REASONS = frozenset(
    {
        REASON_AUTO_CASHOUT_ESCALATION,
        REASON_TRANSFER_ESCALATION,
        REASON_EARLYRB_AUTO_FAILED,
    }
)
FLOW_ANSWER_DECISION_REASONS = frozenset(
    {REASON_EXPECTED_FLOW, REASON_FLOW_CMD, REASON_DEPOSIT_FLOW_ANSWER}
)

_TEST_TITLE_RE = re.compile(r"/\s*(?:1111-1111|2222-2222|8888-8888)\s*/")
TEST_TITLE_EXACT = frozenset({"rt/"})
# A chat whose only non-staff participant is this account is internal.
INTERNAL_ONLY_USERNAMES = frozenset({"rtaccountant"})

_FLOW_COMMANDS = frozenset({"deposit", "cashout", "transfer", "earlyrb", "cash"})
_STAFF_WIZARD_COMMANDS = _FLOW_COMMANDS | {"add", "bonus"}

ROLE_STAFF = "staff"
ROLE_BOT = "bot"
ROLE_PLAYER = "player"
ROLE_UNKNOWN = "unknown_sender"
ROLE_SERVICE = "service"


# ── Text rules ──────────────────────────────────────────────────────────────

_COMMAND_RE = re.compile(r"^/([A-Za-z][A-Za-z0-9_]*)(?:@\w+)?(?:\s|$)")
_AMOUNT_RE = re.compile(
    r"^\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?\s*[kK]?\s*\$?"
    r"(?:\s*(?:usd|dollars?|chips?|credits?))?$",
    re.IGNORECASE,
)
_HANDLE_RE = re.compile(
    r"^(?:@[A-Za-z0-9_.\-]{2,}|\$[A-Za-z][A-Za-z0-9_\-]{0,30}"
    r"|[\w.+\-]+@[\w\-]+\.[\w.\-]+|\+?[\d\s().\-]{10,18})$"
)
_PAY_LINK_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:venmo\.com|account\.venmo\.com|cash\.app|"
    r"paypal\.me|paypal\.com)/\S+$",
    re.IGNORECASE,
)
_HEX_HASH_RE = re.compile(r"(?:0x)?[0-9a-fA-F]{64}")
_LONG_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{32,}$")
_SENT_DONE_WORDS = frozenset(
    {"sent", "done", "i", "just", "have", "it", "payment", "the", "all", "ok", "okay"}
)
_NORMALIZE_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
# Closers: a burst made only of these never opens a player_question. This is the
# one list the audit uses (burst rules and pre-labels); the escalation closer
# list (support_group_idle_episode.is_player_gratitude_ack) is also accepted.
GRATITUDE_PHRASES = (
    "thank you",
    "thanks",
    "thank u",
    "thankyou",
    "thx",
    "ty",
    "tysm",
    "tyvm",
    "appreciate it",
    "appreciate you",
    "appreciated",
)
CLOSER_PHRASES = GRATITUDE_PHRASES + (
    "got it",
    "sounds good",
    "no rush",
    "no worries",
    "all good",
    "good looks",
)
CLOSER_WORDS = frozenset(
    {
        "bet",
        "ok",
        "okay",
        "okey",
        "okk",
        "okkk",
        "k",
        "kk",
        "gotchu",
        "gotcha",
        "perfect",
        "cool",
        "nice",
        "lol",
        "haha",
        "gl",
        "sweet",
        "awesome",
    }
)
# Closers only when answering a staff message from the previous 5 minutes.
CONTEXT_CLOSER_WORDS = frozenset({"sure", "yes", "yep", "yup"})
CONTEXT_CLOSER_WINDOW = timedelta(minutes=5)
# Trailing words allowed after the closers ("okey boss", "thank you g looks").
_MAX_TRAILING_AFTER_GRATITUDE = 6
_MAX_TRAILING_AFTER_CLOSER = 2
_ASK_WORDS = frozenset(
    {
        "where",
        "when",
        "why",
        "how",
        "what",
        "can",
        "could",
        "still",
        "not",
        "didnt",
        "havent",
        "hasnt",
        "wasnt",
        "isnt",
        "yet",
        "cashout",
        "deposit",
        "balance",
        "but",
        "wrong",
        "missing",
        "waiting",
    }
)
_HEAD_ADMIN_RE = re.compile(r"head[\s\-]*admins?", re.IGNORECASE)


def _norm_words(text: str | None) -> str:
    body = _NORMALIZE_RE.sub(" ", (text or "").lower())
    return _SPACE_RE.sub(" ", body).strip()


def command_name(text: str | None) -> str | None:
    m = _COMMAND_RE.match((text or "").strip())
    return m.group(1).lower() if m else None


def is_bot_command(text: str | None) -> bool:
    return command_name(text) is not None


def is_amount_text(text: str | None) -> bool:
    return bool(_AMOUNT_RE.match((text or "").strip()))


def is_handle_text(text: str | None) -> bool:
    """Payout handle: @user, $cashtag, email, phone number or a pay link."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_HANDLE_RE.fullmatch(raw) or _PAY_LINK_RE.match(raw))


def is_hash_like(text: str | None) -> bool:
    """A transaction hash / TxID: 64 hex (optionally 0x), a long base58 token,
    or an explorer link containing a 64-hex hash."""

    raw = (text or "").strip()
    if not raw:
        return False
    if _HEX_HASH_RE.fullmatch(raw):
        return True
    if " " not in raw and _LONG_TOKEN_RE.match(raw):
        return True
    return (
        " " not in raw
        and raw.lower().startswith("http")
        and bool(_HEX_HASH_RE.search(raw))
    )


def is_sent_done_text(text: str | None) -> bool:
    words = _norm_words(text).split()
    if not words or len(words) > 6:
        return False
    return set(words) <= _SENT_DONE_WORDS and bool({"sent", "done"} & set(words))


def is_wizard_answer_text(text: str | None) -> bool:
    """Bare amount, payout handle, TxID or "sent"/"done" — answers, not questions."""

    return (
        is_amount_text(text)
        or is_handle_text(text)
        or is_hash_like(text)
        or is_sent_done_text(text)
    )


def _strip_closers(
    words: list[str], *, replies_to_staff: bool
) -> tuple[list[str], bool]:
    """Drop leading closer phrases / words. Returns (remainder, saw_gratitude)."""

    saw_gratitude = False
    single = CLOSER_WORDS | (CONTEXT_CLOSER_WORDS if replies_to_staff else frozenset())
    changed = True
    while words and changed:
        changed = False
        joined = " ".join(words)
        for phrase in CLOSER_PHRASES:
            if joined == phrase or joined.startswith(phrase + " "):
                words = words[len(phrase.split()) :]
                saw_gratitude = saw_gratitude or phrase in GRATITUDE_PHRASES
                changed = True
                break
        if not changed and words and words[0] in single:
            words = words[1:]
            changed = True
    return words, saw_gratitude


def is_gratitude_or_closer(text: str | None, *, replies_to_staff: bool = False) -> bool:
    """Thanks / ack closer only (``CLOSER_PHRASES`` / ``CLOSER_WORDS``, emoji-only).

    Case, punctuation and emoji are ignored. "sure" / "yes" / "yep" / "yup" count
    only with ``replies_to_staff``. A few trailing words are allowed after the
    closers ("okey boss", "thank you g looks") unless the text reads like a
    question.
    """

    raw = (text or "").strip()
    if not raw:
        return False
    if is_player_gratitude_ack(raw):
        return True
    words = _norm_words(raw).split()
    if not words:
        # Emoji / punctuation only.
        return True
    if "?" in raw or _ASK_WORDS & set(words):
        return False
    rest, saw_gratitude = _strip_closers(words, replies_to_staff=replies_to_staff)
    if len(rest) == len(words):
        return False
    limit = (
        _MAX_TRAILING_AFTER_GRATITUDE
        if saw_gratitude or {"bet", "gl"} & set(words)
        else _MAX_TRAILING_AFTER_CLOSER
    )
    return len(rest) <= limit


def is_test_title(title: str | None) -> bool:
    raw = (title or "").strip()
    if raw.casefold() in TEST_TITLE_EXACT:
        return True
    return bool(_TEST_TITLE_RE.search(raw))


def only_internal_participants(
    messages: Iterable[Any],
    *,
    staff_ids: frozenset[int],
    bot_usernames: frozenset[str],
    chat_id: int,
    player_username: str | None = None,
) -> bool:
    """True when the only non-staff member is an internal account (@rtaccountant).

    Uses the chat's bound player username when set, else the non-staff, non-bot
    senders seen in the transcript (membership lists are not stored).
    """

    bound = (player_username or "").strip().lstrip("@").lower()
    if bound:
        return bound in INTERNAL_ONLY_USERNAMES
    others: set[str] = set()
    for m in messages:
        if not isinstance(m, dict) or m.get("is_service") or m.get("is_bot"):
            continue
        username = (m.get("username") or "").strip().lstrip("@").lower()
        if username in bot_usernames:
            continue
        sender = m.get("sender_id")
        if sender is not None and (
            int(sender) in staff_ids or int(sender) == int(chat_id)
        ):
            continue
        others.add(username or f"id:{sender}")
    return bool(others) and others <= INTERNAL_ONLY_USERNAMES


_OWED_RE = re.compile(r"^\$?\s*[\d,]+(?:\.\d+)?\s+owed$", re.IGNORECASE)


def automated_template_texts(
    extra_texts: Iterable[str | None] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(exact texts, prefixes) of automated staff-account posts (fallback list).

    Used when a message predates ``automated_staff_messages`` rows.
    """

    from bot.services.mtproto_group_cash import CASH_ASAP_MESSAGE
    from bot.services.player_support_dm_messages import (
        PLAYER_ADDED_SUCCESS_MESSAGE,
        PLAYER_EXISTING_INVITE_MESSAGE,
        PLAYER_INVITE_FALLBACK_MESSAGE,
    )
    from club_gc_settings import CLUB_GC_CONFIG

    exact = {CASH_ASAP_MESSAGE.strip().lower()}
    for extra in extra_texts:
        if extra and extra.strip():
            exact.add(extra.strip().lower())
    prefixes = {"group created. invite link"}
    for cfg in CLUB_GC_CONFIG.values():
        head = (cfg.initial_group_message_template or "").split("{", 1)[0].strip()
        if len(head) >= 12:
            prefixes.add(head.lower())
    for tmpl in (
        PLAYER_ADDED_SUCCESS_MESSAGE,
        PLAYER_INVITE_FALLBACK_MESSAGE,
        PLAYER_EXISTING_INVITE_MESSAGE,
    ):
        head = tmpl.split("{", 1)[0].strip()
        if head:
            prefixes.add(head.lower())
    return tuple(sorted(exact)), tuple(sorted(prefixes))


def matches_automated_template(
    text: str | None, exact: Iterable[str], prefixes: Iterable[str]
) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if _OWED_RE.match(raw):
        return True
    low = raw.lower()
    if low in set(exact):
        return True
    return any(low.startswith(p) for p in prefixes)


# ── Rule engine ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlackEscalation:
    id: int
    reason: str
    created_at: datetime


@dataclass
class ChatAuditInput:
    """Everything the rule engine needs for one chat-day (no DB access)."""

    activity_date: date
    chat_id: int
    club_id: int
    group_title: str | None
    messages: list[dict[str, Any]]
    tail_messages: list[dict[str, Any]] = field(default_factory=list)
    staff_ids: frozenset[int] = frozenset()
    bot_usernames: frozenset[str] = frozenset()
    player_id: int | None = None
    automated_kinds: Mapping[int, str] = field(default_factory=dict)
    flow_answer_msg_ids: frozenset[int] = frozenset()
    escalations: list[SlackEscalation] = field(default_factory=list)
    template_exact: tuple[str, ...] = ()
    template_prefixes: tuple[str, ...] = ()
    # Player bursts before this instant belong to a clock still open from D-1.
    carry_open_until: datetime | None = None


@dataclass
class EventDraft:
    start_kind: str
    clock_start_at: datetime
    clock_start_msg_id: int | None = None
    escalation_event_id: int | None = None
    clock_stop_at: datetime | None = None
    clock_stop_msg_id: int | None = None
    responder_telegram_user_id: int | None = None
    trigger_msg_ids: list[int] = field(default_factory=list)
    attached: list[dict[str, Any]] = field(default_factory=list)
    pre_labels: dict[str, Any] = field(default_factory=dict)
    excerpt: list[dict[str, Any]] | None = None
    escalation_reason: str | None = None
    # Closed by the bot's own "payment received" / "Added N" line (ra-2).
    bot_resolved: bool = False

    @property
    def response_seconds(self) -> int | None:
        if self.clock_stop_at is None or self.bot_resolved:
            return None
        # A staff message just before the start (lookback) yields 0.
        return max(
            0, int(round((self.clock_stop_at - self.clock_start_at).total_seconds()))
        )

    @property
    def is_candidate(self) -> bool:
        if self.bot_resolved:
            return False
        secs = self.response_seconds
        return secs is None or secs > CANDIDATE_THRESHOLD_SECONDS


def _msg_dt(msg: Mapping[str, Any]) -> datetime:
    dt = datetime.fromisoformat(str(msg["date"]))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _text(msg: Mapping[str, Any]) -> str:
    return str(msg.get("text") or "").strip()


def _is_media_only(msg: Mapping[str, Any]) -> bool:
    return bool(msg.get("media_type")) and not _text(msg)


def classify_role(msg: Mapping[str, Any], inp: ChatAuditInput) -> str:
    if msg.get("is_service"):
        return ROLE_SERVICE
    username = (msg.get("username") or "").strip().lstrip("@").lower()
    if msg.get("is_bot") or (username and username in inp.bot_usernames):
        return ROLE_BOT
    sender = msg.get("sender_id")
    if sender is None:
        return ROLE_UNKNOWN
    sender = int(sender)
    # Anonymous group admin posts are attributed to the chat itself.
    if sender in inp.staff_ids or sender == int(inp.chat_id):
        return ROLE_STAFF
    if inp.player_id is not None and sender == int(inp.player_id):
        return ROLE_PLAYER
    return ROLE_UNKNOWN


def is_automated_staff_post(msg: Mapping[str, Any], inp: ChatAuditInput) -> bool:
    kind = inp.automated_kinds.get(int(msg["id"]))
    if kind is not None:
        return kind not in STAFF_ACTION_KINDS
    return matches_automated_template(
        _text(msg), inp.template_exact, inp.template_prefixes
    )


def _player_text_qualifies(
    msg: Mapping[str, Any], inp: ChatAuditInput, *, replies_to_staff: bool = False
) -> bool:
    text = _text(msg)
    if not text:
        return False
    if int(msg["id"]) in inp.flow_answer_msg_ids:
        return False
    if is_bot_command(text) or is_wizard_answer_text(text):
        return False
    return not is_gratitude_or_closer(text, replies_to_staff=replies_to_staff)


def is_bot_resolution_text(text: str | None) -> bool:
    """The bot's "We have received your payment…" or "Added N…" line."""

    from bot.services.mtproto_group_add import ADD_CONFIRMATION_PREFIX
    from bot.services.payment_group_notify import (
        PAYMENT_RECEIVED_PREFIX,
        PAYMENT_RECEIVED_SUFFIX,
    )

    raw = (text or "").strip()
    if raw.startswith(PAYMENT_RECEIVED_PREFIX) and raw.endswith(
        PAYMENT_RECEIVED_SUFFIX
    ):
        return True
    rest = raw[len(ADD_CONFIRMATION_PREFIX) :]
    return raw.startswith(ADD_CONFIRMATION_PREFIX) and rest[:1].isdigit()


def _is_txid_prompt(text: str) -> bool:
    if not text:
        return False
    first_line = DEPOSIT_SENT_ACK_COPY_CRYPTO.splitlines()[0].strip()
    return text == DEPOSIT_SENT_ACK_COPY_CRYPTO.strip() or text.startswith(first_line)


def build_chat_events(inp: ChatAuditInput) -> list[EventDraft]:
    """Apply the clock start / stop rules to one chat-day. Pure."""

    day_start, day_end = et_day_window_utc(inp.activity_date)
    seen: set[int] = set()
    msgs: list[dict[str, Any]] = []
    for m in list(inp.messages) + list(inp.tail_messages):
        if not isinstance(m, dict) or m.get("id") is None or m.get("date") is None:
            continue
        mid = int(m["id"])
        if mid in seen:
            continue
        seen.add(mid)
        msgs.append(m)
    msgs.sort(key=lambda m: (_msg_dt(m), int(m["id"])))

    roles = {int(m["id"]): classify_role(m, inp) for m in msgs}
    by_id = {int(m["id"]): m for m in msgs}
    # Human staff replies (the only thing that stops a clock).
    staff_reply_ids = {
        int(m["id"])
        for m in msgs
        if roles[int(m["id"])] == ROLE_STAFF and not is_automated_staff_post(m, inp)
    }
    staff_replies = [(_msg_dt(m), m) for m in msgs if int(m["id"]) in staff_reply_ids]
    resolver_ids = {
        int(m["id"])
        for m in msgs
        if is_bot_resolution_text(_text(m))
        and (
            roles[int(m["id"])] == ROLE_BOT
            or (
                roles[int(m["id"])] == ROLE_STAFF
                and int(m["id"]) not in staff_reply_ids
            )
        )
    }

    def _replies_to_staff(m: Mapping[str, Any]) -> bool:
        reply_to = m.get("reply_to_msg_id")
        if reply_to is not None and int(reply_to) in staff_reply_ids:
            return True
        ts = _msg_dt(m)
        return any(
            timedelta(0) <= ts - sts <= CONTEXT_CLOSER_WINDOW
            for sts, _ in staff_replies
        )

    # Player bursts (bots don't break a burst; staff does).
    bursts: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    for m in msgs:
        role = roles[int(m["id"])]
        if role in (ROLE_PLAYER, ROLE_UNKNOWN):
            if (
                current
                and (_msg_dt(m) - _msg_dt(current[-1])).total_seconds()
                < BURST_GAP_SECONDS
            ):
                current.append(m)
            else:
                current = [m]
                bursts.append(current)
        elif role == ROLE_STAFF:
            current = None
    qualifying_burst_by_first: dict[int, list[dict[str, Any]]] = {}
    burst_of: dict[int, list[dict[str, Any]]] = {}
    for burst in bursts:
        for m in burst:
            burst_of[int(m["id"])] = burst
        if any(
            _player_text_qualifies(m, inp, replies_to_staff=_replies_to_staff(m))
            for m in burst
        ):
            qualifying_burst_by_first[int(burst[0]["id"])] = burst

    # Bot hand-off lines and TxID prompt → hash replies.
    handoff_ids: set[int] = set()
    txid_hash_ids: set[int] = set()
    prompt_at: datetime | None = None
    for m in msgs:
        mid = int(m["id"])
        role = roles[mid]
        text = _text(m)
        if role == ROLE_BOT:
            if text in AGENT_HANDOFF_LINES:
                handoff_ids.add(mid)
            if _is_txid_prompt(text):
                prompt_at = _msg_dt(m)
        elif role in (ROLE_PLAYER, ROLE_UNKNOWN) and prompt_at is not None:
            if _msg_dt(m) - prompt_at > TXID_PROMPT_WINDOW:
                prompt_at = None
            elif is_hash_like(text):
                txid_hash_ids.add(mid)
                prompt_at = None

    # Merge messages and Slack escalations into one timeline. Ignored reasons
    # (the 10-minute deposit reminder) never enter it.
    timeline: list[tuple[datetime, int, Any]] = [(_msg_dt(m), 1, m) for m in msgs]
    for esc in inp.escalations:
        if esc.reason in SLACK_IGNORED_REASONS:
            continue
        if esc.reason in SLACK_START_REASONS or esc.reason in SLACK_ATTACH_ONLY_REASONS:
            timeline.append((_as_utc(esc.created_at), 0, esc))
    timeline.sort(key=lambda t: (t[0], t[1], int(t[2]["id"]) if t[1] else t[2].id))

    events: list[EventDraft] = []
    open_events: list[EventDraft] = []

    def _in_day(ts: datetime) -> bool:
        return day_start <= ts < day_end

    def _close(ev: EventDraft, m: Mapping[str, Any], *, bot: bool = False) -> None:
        ev.clock_stop_at = _msg_dt(m)
        ev.clock_stop_msg_id = int(m["id"])
        ev.bot_resolved = bot
        ev.responder_telegram_user_id = (
            None if bot or m.get("sender_id") is None else int(m["sender_id"])
        )
        if bot:
            ev.pre_labels["bot_resolved"] = True

    def _open_player_question() -> bool:
        return any(ev.start_kind == START_PLAYER_QUESTION for ev in open_events)

    def _attach_target(ts: datetime, *, to_handoff: bool = False) -> EventDraft | None:
        # Signals attach to an open player_question (ra-2: never the other way
        # round). The Slack twin of a hand-off line may also attach to it.
        kinds = {START_PLAYER_QUESTION}
        if to_handoff:
            kinds.add(START_BOT_HANDOFF)
        best = None
        for ev in open_events:
            if ev.start_kind not in kinds:
                continue
            delta = (ts - ev.clock_start_at).total_seconds()
            if 0 <= delta <= ATTACH_WINDOW_SECONDS:
                best = ev
        return best

    def _staff_just_before(ts: datetime) -> Mapping[str, Any] | None:
        """Latest human staff message in the 120 s before ``ts``."""
        best = None
        for sts, m in staff_replies:
            if timedelta(0) <= ts - sts <= timedelta(seconds=STAFF_LOOKBACK_SECONDS):
                best = m
        return best

    def _staff_same_second(ts: datetime) -> Mapping[str, Any] | None:
        second = int(ts.timestamp())
        for sts, m in staff_replies:
            if int(sts.timestamp()) == second:
                return m
        return None

    def _start(ev: EventDraft) -> None:
        events.append(ev)
        if ev.start_kind in (START_SLACK_ESCALATION, START_BOT_HANDOFF):
            prior = _staff_just_before(ev.clock_start_at)
            if prior is not None:
                _close(ev, prior)
                ev.pre_labels["staff_before_escalation"] = True
                return
        same = _staff_same_second(ev.clock_start_at)
        if same is not None:
            _close(ev, same)
            return
        open_events.append(ev)

    def _signal(
        kind: str,
        ts: datetime,
        *,
        msg_id: int | None = None,
        esc: SlackEscalation | None = None,
        start_allowed: bool = True,
    ) -> None:
        target = _attach_target(
            ts,
            to_handoff=esc is not None and esc.reason in SLACK_ATTACH_ONLY_REASONS,
        )
        if target is not None:
            if esc is not None and target.escalation_event_id is None:
                target.escalation_event_id = int(esc.id)
            target.attached.append(
                {
                    "start_kind": kind,
                    "at": ts.isoformat(),
                    "msg_id": msg_id,
                    "escalation_event_id": int(esc.id) if esc else None,
                    "reason": esc.reason if esc else None,
                }
            )
            return
        if not start_allowed or not _in_day(ts):
            return
        ev = EventDraft(
            start_kind=kind,
            clock_start_at=ts,
            clock_start_msg_id=msg_id,
            escalation_event_id=int(esc.id) if esc else None,
            escalation_reason=esc.reason if esc else None,
        )
        if msg_id is not None:
            ev.trigger_msg_ids = [msg_id]
        if esc is not None:
            ev.pre_labels["escalation_reason"] = esc.reason
        _start(ev)

    def _bot_resolvable(ev: EventDraft) -> bool:
        return ev.start_kind == START_PLAYER_QUESTION or (
            ev.start_kind == START_SLACK_ESCALATION
            and ev.escalation_reason in BOT_RESOLVABLE_REASONS
        )

    for ts, is_msg, item in timeline:
        if not is_msg:
            esc: SlackEscalation = item
            _signal(
                START_SLACK_ESCALATION,
                ts,
                esc=esc,
                start_allowed=esc.reason in SLACK_START_REASONS,
            )
            continue
        m = item
        mid = int(m["id"])
        if mid in staff_reply_ids:
            for ev in open_events:
                _close(ev, m)
            open_events.clear()
            continue
        if mid in resolver_ids:
            for ev in list(open_events):
                if _bot_resolvable(ev) and ts - ev.clock_start_at <= BOT_RESOLVE_WINDOW:
                    _close(ev, m, bot=True)
                    open_events.remove(ev)
        if mid in qualifying_burst_by_first:
            carried = inp.carry_open_until is not None and ts < _as_utc(
                inp.carry_open_until
            )
            if not _open_player_question() and not carried and _in_day(ts):
                _start(
                    EventDraft(
                        start_kind=START_PLAYER_QUESTION,
                        clock_start_at=ts,
                        clock_start_msg_id=mid,
                        trigger_msg_ids=[
                            int(x["id"]) for x in qualifying_burst_by_first[mid]
                        ],
                    )
                )
        if mid in handoff_ids:
            _signal(START_BOT_HANDOFF, ts, msg_id=mid)
        if mid in txid_hash_ids:
            _signal(START_CRYPTO_TXID, ts, msg_id=mid)

    for ev in events:
        _fill_trigger_messages(ev, msgs, roles, burst_of, by_id)
        ev.pre_labels.update(_pre_labels(ev, msgs, roles, by_id, inp))
        if ev.attached:
            ev.pre_labels["attached"] = ev.attached
        if ev.is_candidate:
            ev.excerpt = build_excerpt(ev, msgs, roles)
    return events


def _fill_trigger_messages(
    ev: EventDraft,
    msgs: list[dict[str, Any]],
    roles: Mapping[int, str],
    burst_of: Mapping[int, list[dict[str, Any]]],
    by_id: Mapping[int, dict[str, Any]],
) -> None:
    """Player messages that explain why the clock started (for pre-labels)."""

    if ev.start_kind == START_PLAYER_QUESTION:
        return
    if ev.start_kind == START_CRYPTO_TXID:
        return
    # bot_handoff / slack_escalation: the player burst just before the start.
    lookback = SLACK_TRIGGER_LOOKBACK
    recent = [
        m
        for m in msgs
        if roles[int(m["id"])] in (ROLE_PLAYER, ROLE_UNKNOWN)
        and timedelta(0) <= ev.clock_start_at - _msg_dt(m) <= lookback
    ]
    if recent:
        burst = burst_of.get(int(recent[-1]["id"])) or [recent[-1]]
        ev.trigger_msg_ids = [int(m["id"]) for m in burst if int(m["id"]) in by_id]


def _pre_labels(
    ev: EventDraft,
    msgs: list[dict[str, Any]],
    roles: Mapping[int, str],
    by_id: Mapping[int, dict[str, Any]],
    inp: ChatAuditInput,
) -> dict[str, Any]:
    triggers = [by_id[i] for i in ev.trigger_msg_ids if i in by_id]
    texts = [_text(m) for m in triggers if _text(m)]
    gratitude_only = bool(texts) and all(is_gratitude_or_closer(t) for t in texts)
    media_only = bool(triggers) and all(_is_media_only(m) for m in triggers)
    unknown_sender = any(roles[int(m["id"])] == ROLE_UNKNOWN for m in triggers)

    start = ev.clock_start_at
    stop = ev.clock_stop_at
    in_bot_flow = any(int(m["id"]) in inp.flow_answer_msg_ids for m in triggers)
    staff_drove_wizard = False
    staff_pinged_head_admins = False
    window_end = stop or (start + STAFF_WIZARD_WINDOW)
    for m in msgs:
        ts = _msg_dt(m)
        cmd = command_name(_text(m))
        role = roles[int(m["id"])]
        if cmd in _FLOW_COMMANDS and timedelta(0) <= start - ts <= FLOW_WINDOW:
            in_bot_flow = True
        if (
            role == ROLE_STAFF
            and cmd in _STAFF_WIZARD_COMMANDS
            and start - STAFF_WIZARD_WINDOW <= ts <= window_end
        ):
            staff_drove_wizard = True
        if (
            role == ROLE_STAFF
            and start <= ts <= window_end + EXCERPT_AFTER
            and _HEAD_ADMIN_RE.search(_text(m))
        ):
            staff_pinged_head_admins = True
    return {
        "gratitude_only": gratitude_only,
        "in_bot_flow": in_bot_flow,
        "media_only": media_only,
        "staff_drove_wizard": staff_drove_wizard,
        "staff_pinged_head_admins": staff_pinged_head_admins,
        "unknown_sender": unknown_sender,
    }


def build_excerpt(
    ev: EventDraft,
    msgs: list[dict[str, Any]],
    roles: Mapping[int, str],
) -> list[dict[str, Any]]:
    """Messages from 20 min before clock start to 5 min after stop (max 60).

    Unanswered events run to the end of the tail. When trimming, keep the
    messages closest to the clock start and always the stop message.
    """

    lo = ev.clock_start_at - EXCERPT_BEFORE
    hi = (ev.clock_stop_at + EXCERPT_AFTER) if ev.clock_stop_at else None
    window = [m for m in msgs if _msg_dt(m) >= lo and (hi is None or _msg_dt(m) <= hi)]
    if len(window) > EXCERPT_MAX_MESSAGES:
        before = [m for m in window if _msg_dt(m) < ev.clock_start_at]
        after = [m for m in window if _msg_dt(m) >= ev.clock_start_at]
        keep_before = before[-15:]
        stop = [m for m in after if int(m["id"]) == ev.clock_stop_msg_id]
        budget = EXCERPT_MAX_MESSAGES - len(keep_before) - len(stop)
        core = [m for m in after if int(m["id"]) != ev.clock_stop_msg_id][:budget]
        window = sorted(
            keep_before + core + stop, key=lambda m: (_msg_dt(m), int(m["id"]))
        )
    out = []
    for m in window:
        row = dict(m)
        row["role"] = roles.get(int(m["id"]), ROLE_UNKNOWN)
        out.append(row)
    return out


# ── DB orchestration ────────────────────────────────────────────────────────


@dataclass
class AuditRunSummary:
    activity_date: date
    chats_scanned: int = 0
    chats_excluded: int = 0
    events: int = 0
    candidates: int = 0
    skipped_existing: bool = False


def _group_titles(session, chat_ids: list[int]) -> dict[int, str]:
    from db.models import Group, SupportGroupChat

    titles: dict[int, str] = {}
    for row in session.query(SupportGroupChat).filter(
        SupportGroupChat.telegram_chat_id.in_(chat_ids)
    ):
        if (row.telegram_chat_title or "").strip():
            titles[int(row.telegram_chat_id)] = row.telegram_chat_title.strip()
    for g in session.query(Group).filter(Group.chat_id.in_(chat_ids)):
        if (g.name or "").strip():
            titles[int(g.chat_id)] = g.name.strip()
    return titles


def _load_chat_inputs(
    activity_date: date,
    *,
    chat_id: int | None,
) -> tuple[list[ChatAuditInput], int]:
    """Build rule-engine inputs from Postgres. Returns (inputs, excluded count)."""

    from bot.services.automated_staff_messages import load_automated_message_kinds
    from bot.services.player_details import is_gc_group_title
    from bot.services.response_audit_staff import bot_usernames, load_staff_ids
    from db.connection import get_db
    from db.models import (
        Club,
        EscalationDecisionLog,
        EscalationEvent,
        GroupChatDailyTranscript,
        ResponseEvent,
        SupportGroupChat,
    )

    day_start, _day_end = et_day_window_utc(activity_date)
    _tail_start, tail_end = et_tail_window_utc(activity_date)
    staff_ids = load_staff_ids()
    bots = bot_usernames()

    inputs: list[ChatAuditInput] = []
    excluded = 0
    with get_db() as session:
        q = session.query(GroupChatDailyTranscript).filter(
            GroupChatDailyTranscript.activity_date == activity_date,
            GroupChatDailyTranscript.status == STATUS_COMPLETE,
        )
        if chat_id is not None:
            q = q.filter(GroupChatDailyTranscript.chat_id == int(chat_id))
        transcripts = q.order_by(GroupChatDailyTranscript.chat_id).all()
        chat_ids = [int(t.chat_id) for t in transcripts]
        titles = _group_titles(session, chat_ids) if chat_ids else {}
        sgc_by_chat: dict[int, Any] = {}
        if chat_ids:
            for row in (
                session.query(SupportGroupChat)
                .filter(SupportGroupChat.telegram_chat_id.in_(chat_ids))
                .order_by(SupportGroupChat.created_at)
            ):
                sgc_by_chat[int(row.telegram_chat_id)] = row
        clubs = {int(c.id): c for c in session.query(Club).all()}

        for t in transcripts:
            cid = int(t.chat_id)
            title = titles.get(cid)
            sgc = sgc_by_chat.get(cid)
            if sgc is not None and bool(getattr(sgc, "is_internal", False)):
                excluded += 1
                continue
            if is_test_title(title):
                excluded += 1
                continue
            if only_internal_participants(
                [m for m in (t.messages or []) + (t.tail_messages or [])],
                staff_ids=staff_ids,
                bot_usernames=bots,
                chat_id=cid,
                player_username=getattr(sgc, "player_username", None),
            ):
                excluded += 1
                continue
            if sgc is None and not is_gc_group_title(title):
                # Club-linked but not a player support group (ops / union chats).
                excluded += 1
                continue

            escalations = [
                SlackEscalation(
                    id=int(e.id), reason=str(e.reason), created_at=e.created_at
                )
                for e in session.query(EscalationEvent).filter(
                    EscalationEvent.telegram_chat_id == cid,
                    EscalationEvent.created_at >= day_start - EXCERPT_BEFORE,
                    EscalationEvent.created_at < tail_end,
                )
            ]
            flow_ids = frozenset(
                int(r.telegram_message_id)
                for r in session.query(EscalationDecisionLog).filter(
                    EscalationDecisionLog.telegram_chat_id == cid,
                    EscalationDecisionLog.decision == DECISION_SKIPPED,
                    EscalationDecisionLog.reason.in_(
                        sorted(FLOW_ANSWER_DECISION_REASONS)
                    ),
                    EscalationDecisionLog.created_at >= day_start - EXCERPT_BEFORE,
                    EscalationDecisionLog.created_at < tail_end + EXCERPT_AFTER,
                    EscalationDecisionLog.telegram_message_id.isnot(None),
                )
            )
            carry = _carry_open_until(session, ResponseEvent, activity_date, cid)
            club = clubs.get(int(t.club_id))
            exact, prefixes = automated_template_texts(
                [
                    getattr(club, "welcome_caption", None),
                    getattr(club, "welcome_text", None),
                    getattr(club, "member_join_preamble_text", None),
                    getattr(club, "member_join_tos_caption", None),
                ]
            )
            inputs.append(
                ChatAuditInput(
                    activity_date=activity_date,
                    chat_id=cid,
                    club_id=int(t.club_id),
                    group_title=title,
                    messages=[m for m in (t.messages or []) if isinstance(m, dict)],
                    tail_messages=[
                        m for m in (t.tail_messages or []) if isinstance(m, dict)
                    ],
                    staff_ids=staff_ids,
                    bot_usernames=bots,
                    player_id=(
                        int(sgc.player_telegram_user_id)
                        if sgc is not None and sgc.player_telegram_user_id
                        else None
                    ),
                    automated_kinds={},
                    flow_answer_msg_ids=flow_ids,
                    escalations=escalations,
                    template_exact=exact,
                    template_prefixes=prefixes,
                    carry_open_until=carry,
                )
            )
    for inp in inputs:
        inp.automated_kinds = load_automated_message_kinds(inp.chat_id)
    return inputs, excluded


def _carry_open_until(session, model, activity_date: date, chat_id: int):
    """End of a D-1 clock still running into day D (None when none)."""

    prev = activity_date - timedelta(days=1)
    rows = (
        session.query(model)
        .filter(model.activity_date == prev, model.chat_id == int(chat_id))
        .all()
    )
    day_start, _ = et_day_window_utc(activity_date)
    _tail_start, prev_tail_end = et_tail_window_utc(prev)
    latest = None
    for r in rows:
        end = _as_utc(r.clock_stop_at) if r.clock_stop_at else prev_tail_end
        if end > day_start and (latest is None or end > latest):
            latest = end
    return latest


def _natural_key(start_kind: str, msg_id: int | None, esc_id: int | None) -> tuple:
    return (start_kind, msg_id if msg_id is not None else f"esc:{esc_id}")


def persist_chat_events(inp: ChatAuditInput, drafts: list[EventDraft]) -> None:
    """Upsert this chat-day's events by natural key; drop stale ones.

    Re-runs keep row ids (and any judge verdicts) for events that still exist.
    """

    from db.connection import get_db
    from db.models import ResponseEvent

    with get_db() as session:
        existing = {
            _natural_key(r.start_kind, r.clock_start_msg_id, r.escalation_event_id): r
            for r in session.query(ResponseEvent).filter(
                ResponseEvent.activity_date == inp.activity_date,
                ResponseEvent.chat_id == int(inp.chat_id),
            )
        }
        keep: set[int] = set()
        for d in drafts:
            key = _natural_key(
                d.start_kind, d.clock_start_msg_id, d.escalation_event_id
            )
            row = existing.get(key)
            if row is None:
                row = ResponseEvent(
                    activity_date=inp.activity_date,
                    chat_id=int(inp.chat_id),
                    start_kind=d.start_kind,
                )
                session.add(row)
            row.club_id = int(inp.club_id)
            row.group_title = inp.group_title
            row.clock_start_at = d.clock_start_at
            row.clock_start_msg_id = d.clock_start_msg_id
            row.escalation_event_id = d.escalation_event_id
            row.clock_stop_at = d.clock_stop_at
            row.clock_stop_msg_id = d.clock_stop_msg_id
            row.responder_telegram_user_id = d.responder_telegram_user_id
            row.response_seconds = d.response_seconds
            row.is_candidate = d.is_candidate
            row.pre_labels = d.pre_labels
            row.excerpt = d.excerpt
            row.rule_version = RULE_VERSION
            session.flush()
            keep.add(int(row.id))
        for row in existing.values():
            if int(row.id) not in keep:
                session.delete(row)


def _record_run(summary: AuditRunSummary) -> None:
    from db.connection import get_db
    from db.models import ResponseAuditRun

    with get_db() as session:
        row = session.get(ResponseAuditRun, summary.activity_date)
        if row is None:
            row = ResponseAuditRun(activity_date=summary.activity_date)
            session.add(row)
        row.ran_at = datetime.now(timezone.utc)
        row.rule_version = RULE_VERSION
        row.chats_scanned = summary.chats_scanned
        row.chats_excluded = summary.chats_excluded
        row.events = summary.events
        row.candidates = summary.candidates


def audit_already_ran(activity_date: date) -> bool:
    from db.connection import get_db
    from db.models import ResponseAuditRun

    with get_db() as session:
        return session.get(ResponseAuditRun, activity_date) is not None


def run_response_audit(
    activity_date: date,
    *,
    chat_id: int | None = None,
    force: bool = False,
) -> AuditRunSummary:
    """Build ``response_events`` for one ET day (idempotent per chat-day).

    Without ``force`` a day that already has a run marker is left alone (the
    nightly cron always builds a fresh day). ``chat_id`` limits the rebuild to
    one chat; the day's run marker is only rewritten on a full-day run.
    """

    summary = AuditRunSummary(activity_date=activity_date)
    if not force and chat_id is None and audit_already_ran(activity_date):
        summary.skipped_existing = True
        return summary

    inputs, excluded = _load_chat_inputs(activity_date, chat_id=chat_id)
    summary.chats_excluded = excluded
    for inp in inputs:
        try:
            drafts = build_chat_events(inp)
            persist_chat_events(inp, drafts)
        except Exception:
            logger.exception(
                "response_audit: chat failed chat_id=%s date=%s",
                inp.chat_id,
                activity_date,
            )
            continue
        summary.chats_scanned += 1
        summary.events += len(drafts)
        summary.candidates += sum(1 for d in drafts if d.is_candidate)

    if chat_id is None:
        _record_run(summary)
    logger.info(
        "response_audit: done date=%s chats=%s excluded=%s events=%s candidates=%s",
        activity_date,
        summary.chats_scanned,
        summary.chats_excluded,
        summary.events,
        summary.candidates,
    )
    return summary
