"""RecruitFlow-Core U1: pure state machine core (recruitflow_engine).
Per GPAI ADD AAL-ADD-20261029-TK85-V1.0 §4/§5/§9-U1. Pure functions only; no I/O, no DB.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional


# ---------- states & events ----------

class State(str, enum.Enum):
    NEW = "NEW"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    NO_SHOW_PENDING = "NO_SHOW_PENDING"
    LINE_ROUTED = "LINE_ROUTED"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    REJECTED_BLACKLIST = "REJECTED_BLACKLIST"
    REJECTED_QUALIFICATION = "REJECTED_QUALIFICATION"


class Event(str, enum.Enum):
    E_SCREEN = "E_SCREEN"
    E_CONTACT_RESULT = "E_CONTACT_RESULT"
    E_INTENT_ACCEPT = "E_INTENT_ACCEPT"
    E_INTENT_REJECT = "E_INTENT_REJECT"
    E_CHECKIN = "E_CHECKIN"
    E_CHECK_TIMEOUT = "E_CHECK_TIMEOUT"
    E_RESCHEDULE = "E_RESCHEDULE"
    E_GIVE_UP = "E_GIVE_UP"
    E_WITHDRAW = "E_WITHDRAW"
    E_REJECTION_DUE = "E_REJECTION_DUE"


TERMINAL_STATES = frozenset({
    State.REJECTED_BLACKLIST, State.LINE_ROUTED, State.COMPLETED, State.CLOSED,
})

# contact result sub-codes (E_CONTACT_RESULT payload)
CONTACT_NO_ANSWER = "NO_ANSWER"
CONTACT_FAILED = "FAILED"
CONTACT_UNKNOWN = "UNKNOWN"


class TransitionRejected(Exception):
    """Illegal transition: event not allowed in current state (ADD §4 last row)."""

    def __init__(self, state: State, event: Event, reason: str):
        self.state, self.event, self.reason = state, event, reason
        super().__init__(f"rejected: {event.value} in {state.value}: {reason}")


class GuardFailed(Exception):
    """Guard not satisfied: event allowed in state but facts fail the guard."""


# ---------- policy / facts / clock ----------

@dataclass(frozen=True)
class Policy:
    """Versioned, deterministic policy (ADD §4: 評分/規則版本化; §5 deadline 規則)."""
    version: str = "p1.1"
    grace_minutes: int = 30            # deadline = scheduled_at + 30 min; now>deadline 才逾時
    no_show_close_hours: int = 24      # NO_SHOW_PENDING 24h 關閉 timer
    draft_hours: int = 72              # REJECTED_QUALIFICATION 72h 草稿
    schedule_window_hours: int = 12    # ADD 暫定窗口：預約/改期必須 (now, now+12h] 內（U1.1 修復）
    line_url: str = "https://line.example/R/"  # allowlist 固定設定


@dataclass(frozen=True)
class Clock:
    """Deterministic clock: epoch milliseconds (ADD: UTC epoch ms)."""
    now_ms: int


@dataclass(frozen=True)
class Facts:
    """Per-event facts evaluated by guards. 缺資料≠誤拒：missing fields are None."""
    blacklist_hit: Optional[bool] = None
    data_complete: Optional[bool] = None          # None = 無法可靠解析 → 留 NEW 補件
    qualification_pass: Optional[bool] = None
    contact_result: Optional[str] = None          # NO_ANSWER / FAILED / UNKNOWN
    scheduled_at_ms: Optional[int] = None         # current interview revision start
    now_over_deadline: Optional[bool] = None      # precomputed: now > scheduled_at+grace (strict >)
    interview_revision: Optional[int] = None      # event's revision
    current_revision: Optional[int] = None        # application's current revision
    hr_confirmed: Optional[bool] = None
    new_scheduled_at_ms: Optional[int] = None
    region_mapped: Optional[bool] = None
    slots_available: Optional[bool] = None
    response_deadline_passed: Optional[bool] = None  # NO_SHOW 24h: now > entered+24h (strict >)
    late_attendance: Optional[bool] = None           # HR 確認遲到到場


@dataclass
class TransitionResult:
    to_state: State
    version_bump: bool = True
    effects: tuple = field(default_factory=tuple)   # ("outbox", event_type, payload) / ("job", kind, payload) / ("mark", label)
    audit: str = ""


# ---------- guards (ADD §4 table, row by row) ----------

def _req(f: Facts, name: str) -> Any:
    """缺資料不誤拒：guard 資料不足 → GuardFailed（上層處理），不以 None 當 False。"""
    v = getattr(f, name)
    if v is None:
        raise GuardFailed(f"missing fact: {name}")
    return v


def _stale_revision(f: Facts) -> bool:
    """改期後舊 revision 失效：event revision 與 current revision 不一致 → 舊事件作廢。
    U1.1 fail-closed：任一 revision 缺漏 = 無法確認相符 → GuardFailed（不得當作「未過期」放行）。"""
    if f.interview_revision is None or f.current_revision is None:
        raise GuardFailed("missing interview_revision/current_revision; cannot confirm match")
    return f.interview_revision != f.current_revision


def _validate_scheduled_time(at_ms: int, policy: Policy, clock: Clock) -> None:
    """U1.1 合法時段 guard：預約/改期時間必須在未來且不超過 policy.schedule_window_hours 窗口。
    過去時間或超出窗口 → GuardFailed（上層重挑時段）。"""
    if at_ms <= clock.now_ms:
        raise GuardFailed(f"scheduled time {at_ms} is not in the future (now={clock.now_ms})")
    window_ms = policy.schedule_window_hours * 3600000
    if at_ms > clock.now_ms + window_ms:
        raise GuardFailed(f"scheduled time {at_ms} beyond {policy.schedule_window_hours}h window")


# ---------- pure transition ----------

def transition(state: State, event: Event, facts: Facts, policy: Policy = Policy(), clock: Clock = Clock(0)) -> TransitionResult:
    """Pure function per GPAI ADD §4 transition table. Raises TransitionRejected / GuardFailed."""
    if not isinstance(state, State) or not isinstance(event, Event):
        raise TypeError("state/event must be State/Event enums")

    # 全域規則：撤回在任何非終止態可關閉（ADD §4 E_WITHDRAW row）
    if event is Event.E_WITHDRAW:
        if state in TERMINAL_STATES or state is State.REJECTED_QUALIFICATION:
            # ADD 列 NEW/PENDING/INTERVIEW/NO_SHOW/REJECTED_QUALIFICATION 可撤回；終止態重複/非法
            if state in TERMINAL_STATES:
                raise TransitionRejected(state, event, "already terminal")
        return TransitionResult(
            to_state=State.CLOSED,
            effects=(("job", "cancel_all", {"application_closed": True}),
                     ("outbox", "Outbox.Cancel", {"scope": "undelivered_only"})),  # 已送出不能假撤回
            audit=f"withdraw from {state.value} at {clock.now_ms}",
        )

    # 終止態：重複事件回原 receipt 由上層 receipt 處理；這裡明確拒絕新轉移
    if state in TERMINAL_STATES:
        raise TransitionRejected(state, event, "terminal state")

    # ---------- NEW ----------
    if state is State.NEW:
        if event is not Event.E_SCREEN:
            raise TransitionRejected(state, event, "NEW only accepts E_SCREEN")
        # 1) 黑名單優先（在所有資格/分數之前）
        if facts.blacklist_hit is True:
            return TransitionResult(State.REJECTED_BLACKLIST, effects=(("mark", "blacklist_block"),),
                                    audit="blacklist hit (priority 1)")
        # 1b) U1.1 保守處理：黑名單結果未知（None）＝不得放行，留 NEW 人工確認
        if facts.blacklist_hit is None:
            return TransitionResult(State.NEW, effects=(("mark", "blacklist_manual_check_required"),),
                                    audit="blacklist result unknown; stay NEW for manual check")
        # 2) 缺資料/無法解析（None=無法可靠解析；False=明確缺漏）：留 NEW，人工補件，不當資格不合
        if facts.data_complete is not True:
            return TransitionResult(State.NEW, effects=(("mark", "manual_completion_required"),),
                                    audit="incomplete/unresolvable data; stay NEW")
        # 3) 資格不符
        if _req(facts, "qualification_pass") is False:
            return TransitionResult(
                State.REJECTED_QUALIFICATION,
                effects=(("job", "draft_72h", {"due_ms": clock.now_ms + policy.draft_hours * 3600000}),),
                audit=f"qualification fail; 72h draft due at {clock.now_ms + policy.draft_hours * 3600000}")
        # 4) 通過
        return TransitionResult(State.PENDING_CONFIRMATION,
                                effects=(("outbox", "Contact.Requested", {"mode": "mock"}),),
                                audit="screen passed; contact requested")

    # ---------- PENDING_CONFIRMATION ----------
    if state is State.PENDING_CONFIRMATION:
        if event is Event.E_CONTACT_RESULT:
            r = _req(facts, "contact_result")
            if r == CONTACT_NO_ANSWER:
                # 一次 SMS 轉接，按 contact attempt ID 去重（去重由 receipt 層；這裡只產生一次效果）
                return TransitionResult(State.PENDING_CONFIRMATION,
                                        effects=(("outbox", "SMS.Requested", {"dedupe": "contact_attempt_id"}),),
                                        audit="no answer; one SMS requested")
            if r == CONTACT_FAILED or r == CONTACT_UNKNOWN:
                return TransitionResult(State.PENDING_CONFIRMATION, effects=(("mark", "manual_handling"),),
                                        audit=f"contact {r}; manual handling")
            raise GuardFailed(f"unknown contact_result: {r}")
        if event is Event.E_INTENT_REJECT:
            return TransitionResult(State.LINE_ROUTED,
                                    effects=(("outbox", "LINE.Route", {"url": policy.line_url, "note": "not_join_proof"}),),
                                    audit="intent reject; LINE routed (allowlist URL)")
        if event is Event.E_INTENT_ACCEPT:
            if _req(facts, "slots_available") is False or _req(facts, "region_mapped") is False:
                return TransitionResult(State.PENDING_CONFIRMATION, effects=(("mark", "hr_todo_no_slot_or_region"),),
                                        audit="accept but no slot/region; HR todo")
            if _req(facts, "hr_confirmed") is not True:
                return TransitionResult(State.PENDING_CONFIRMATION, effects=(("mark", "hr_confirmation_pending"),),
                                        audit="accept pending HR confirmation")
            # U1.1 合法時段 guard：不得預約過去時間或超出窗口
            _validate_scheduled_time(_req(facts, "scheduled_at_ms"), policy, clock)
            return TransitionResult(
                State.INTERVIEW_SCHEDULED,
                effects=(("job", "no_show", {"scheduled_at_ms": _req(facts, "scheduled_at_ms"),
                                             "revision": _req(facts, "current_revision")}),
                         ("outbox", "Calendar.MockACK", {})),
                audit="interview scheduled with no-show timer")
        raise TransitionRejected(state, event, "PENDING accepts E_CONTACT_RESULT/E_INTENT_*")
    # ---------- INTERVIEW_SCHEDULED ----------
    if state is State.INTERVIEW_SCHEDULED:
        if event is Event.E_CHECKIN:
            if _stale_revision(facts):
                raise TransitionRejected(state, event, "stale interview revision")
            # 有效報到且不晚於 grace deadline（now=deadline 仍可報到：由 caller 以 now_over_deadline=now>deadline 判定）
            if _req(facts, "now_over_deadline") is True:
                raise GuardFailed("past grace deadline; use NO_SHOW flow for late attendance")
            return TransitionResult(State.COMPLETED, effects=(("job", "cancel_no_show", {}),),
                                    audit="checked in on time")
        if event is Event.E_CHECK_TIMEOUT:
            if _req(facts, "now_over_deadline") is not True:
                raise GuardFailed("deadline not passed; cannot no-show")
            if _stale_revision(facts):
                raise TransitionRejected(state, event, "stale revision")
            return TransitionResult(
                State.NO_SHOW_PENDING,
                effects=(("outbox", "Reschedule.Inquiry", {"once": True}),
                         ("job", "no_show_close_24h", {"entered_ms": clock.now_ms})),
                audit="no-show pending; inquiry + 24h close timer")
        if event is Event.E_RESCHEDULE:
            if _stale_revision(facts):
                raise TransitionRejected(state, event, "stale revision")
            return _do_reschedule(state, facts, policy, clock)
        raise TransitionRejected(state, event, "INTERVIEW_SCHEDULED accepts E_CHECKIN/E_CHECK_TIMEOUT/E_RESCHEDULE")

    # ---------- NO_SHOW_PENDING ----------
    if state is State.NO_SHOW_PENDING:
        if event is Event.E_CHECKIN:
            if _stale_revision(facts):
                raise TransitionRejected(state, event, "stale revision")
            # 遲到到場：HR 確認且未換期 → COMPLETED（ADD 補上的遲到分支）
            if _req(facts, "late_attendance") is True and _req(facts, "hr_confirmed") is True:
                return TransitionResult(State.COMPLETED, effects=(("job", "cancel_close_24h", {}),),
                                        audit="late attendance confirmed by HR; completed")
            raise GuardFailed("late attendance requires hr_confirmed")
        if event is Event.E_RESCHEDULE:
            if _stale_revision(facts):
                raise TransitionRejected(state, event, "stale revision")
            return _do_reschedule(state, facts, policy, clock)
        if event is Event.E_GIVE_UP:
            if _req(facts, "response_deadline_passed") is not True:
                raise GuardFailed("24h response deadline not passed")
            return TransitionResult(State.CLOSED, effects=(("mark", "no_response_closed"),),
                                    audit="no response within 24h; closed")
        raise TransitionRejected(state, event, "NO_SHOW_PENDING accepts E_CHECKIN/E_RESCHEDULE/E_GIVE_UP")

    # ---------- REJECTED_QUALIFICATION（非終止態） ----------
    if state is State.REJECTED_QUALIFICATION:
        if event is Event.E_REJECTION_DUE:
            # 72h 到期且未撤回：產生一次草稿（需 HR 核准才真送出）
            return TransitionResult(State.REJECTED_QUALIFICATION, version_bump=True,
                                    effects=(("outbox", "Draft.Created", {"needs_hr_approval": True, "once": True}),),
                                    audit="72h due; draft created (HR approval gate)")
        raise TransitionRejected(state, event, "REJECTED_QUALIFICATION accepts E_REJECTION_DUE/E_WITHDRAW")

    raise TransitionRejected(state, event, "unhandled")


def _do_reschedule(state: State, facts: Facts, policy: Policy, clock: Clock) -> TransitionResult:
    """NO_SHOW_PENDING/INTERVIEW_SCHEDULED → 改期：新 revision、舊 timers 撤銷。"""
    if _req(facts, "hr_confirmed") is not True:
        raise GuardFailed("reschedule requires hr_confirmed")
    new_at = _req(facts, "new_scheduled_at_ms")
    # U1.1 合法時段 guard：改期同樣不得排入過去或超出窗口
    _validate_scheduled_time(new_at, policy, clock)
    new_rev = _req(facts, "current_revision")  # caller assigns incremented revision in current_revision
    return TransitionResult(
        State.INTERVIEW_SCHEDULED,
        effects=(("job", "cancel_timers", {"old_revision": True}),
                 ("job", "no_show", {"scheduled_at_ms": new_at, "revision": new_rev})),
        audit=f"rescheduled to {new_at}, revision {new_rev}",
    )
