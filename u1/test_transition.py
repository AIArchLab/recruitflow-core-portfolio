"""U1 tests for recruitflow_engine — GPAI ADD §4 table + §5 boundaries, deterministic clock.
Run: python -m pytest u1/test_transition.py -q   (or: python -m unittest u1.test_transition)
"""
import unittest
from datetime import datetime, timezone

from recruitflow_engine import (
    Clock, Event, Facts, GuardFailed, Policy, State, TransitionRejected, transition,
)

S, E = State, Event
BASE = int(datetime(2026, 11, 3, 9, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)  # deterministic
P = Policy()


def ok(**kw) -> Facts:
    return Facts(**kw)


class TestScreeningPriority(unittest.TestCase):
    def test_blacklist_first_before_all(self):
        # blacklist hit even when qualification would pass
        r = transition(S.NEW, E.E_SCREEN, ok(blacklist_hit=True, data_complete=True, qualification_pass=True),
                       P, Clock(BASE))
        self.assertEqual(r.to_state, S.REJECTED_BLACKLIST)
        self.assertIn(("mark", "blacklist_block"), r.effects)

    def test_blacklist_false_proceeds(self):
        r = transition(S.NEW, E.E_SCREEN, ok(blacklist_hit=False, data_complete=True, qualification_pass=True), P, Clock(BASE))
        self.assertEqual(r.to_state, S.PENDING_CONFIRMATION)
        self.assertIn(("outbox", "Contact.Requested", {"mode": "mock"}), r.effects)

    def test_incomplete_data_stays_new_not_rejected(self):
        r = transition(S.NEW, E.E_SCREEN, ok(blacklist_hit=False, data_complete=False), P, Clock(BASE))
        self.assertEqual(r.to_state, S.NEW)
        self.assertIn(("mark", "manual_completion_required"), r.effects)

    def test_unresolvable_data_stays_new(self):
        r = transition(S.NEW, E.E_SCREEN, ok(blacklist_hit=False, data_complete=None), P, Clock(BASE))
        self.assertEqual(r.to_state, S.NEW)

    def test_qualification_fail_schedules_72h_draft(self):
        r = transition(S.NEW, E.E_SCREEN, ok(blacklist_hit=False, data_complete=True, qualification_pass=False), P, Clock(BASE))
        self.assertEqual(r.to_state, S.REJECTED_QUALIFICATION)
        self.assertIn(("job", "draft_72h", {"due_ms": BASE + 72 * 3600000}), r.effects)


class TestPendingConfirmation(unittest.TestCase):
    def test_no_answer_one_sms(self):
        r = transition(S.PENDING_CONFIRMATION, E.E_CONTACT_RESULT, ok(contact_result="NO_ANSWER"), P, Clock(BASE))
        self.assertEqual(r.to_state, S.PENDING_CONFIRMATION)
        self.assertIn(("outbox", "SMS.Requested", {"dedupe": "contact_attempt_id"}), r.effects)

    def test_failed_unknown_manual(self):
        for code in ("FAILED", "UNKNOWN"):
            r = transition(S.PENDING_CONFIRMATION, E.E_CONTACT_RESULT, ok(contact_result=code), P, Clock(BASE))
            self.assertEqual(r.to_state, S.PENDING_CONFIRMATION)
            self.assertIn(("mark", "manual_handling"), r.effects)

    def test_intent_reject_routes_line(self):
        r = transition(S.PENDING_CONFIRMATION, E.E_INTENT_REJECT, ok(), P, Clock(BASE))
        self.assertEqual(r.to_state, S.LINE_ROUTED)

    def test_accept_without_slot_stays_pending(self):
        r = transition(S.PENDING_CONFIRMATION, E.E_INTENT_ACCEPT, ok(slots_available=False, region_mapped=True), P, Clock(BASE))
        self.assertEqual(r.to_state, S.PENDING_CONFIRMATION)
        self.assertIn(("mark", "hr_todo_no_slot_or_region"), r.effects)

    def test_accept_without_hr_confirm_stays_pending(self):
        r = transition(S.PENDING_CONFIRMATION, E.E_INTENT_ACCEPT, ok(slots_available=True, region_mapped=True, hr_confirmed=False), P, Clock(BASE))
        self.assertEqual(r.to_state, S.PENDING_CONFIRMATION)

    def test_accept_confirmed_schedules_no_show_job(self):
        r = transition(S.PENDING_CONFIRMATION, E.E_INTENT_ACCEPT,
                       ok(slots_available=True, region_mapped=True, hr_confirmed=True, scheduled_at_ms=BASE + 3600000, current_revision=1), P, Clock(BASE))
        self.assertEqual(r.to_state, S.INTERVIEW_SCHEDULED)
        self.assertIn(("job", "no_show", {"scheduled_at_ms": BASE + 3600000, "revision": 1}), r.effects)


class TestInterviewCheckinBoundary(unittest.TestCase):
    def test_checkin_at_deadline_is_on_time(self):
        # now_over_deadline is strict > : at deadline it is False
        r = transition(S.INTERVIEW_SCHEDULED, E.E_CHECKIN, ok(now_over_deadline=False, interview_revision=1, current_revision=1), P, Clock(BASE))
        self.assertEqual(r.to_state, S.COMPLETED)

    def test_checkin_after_deadline_guard(self):
        with self.assertRaises(GuardFailed):
            transition(S.INTERVIEW_SCHEDULED, E.E_CHECKIN, ok(now_over_deadline=True, interview_revision=1, current_revision=1), P, Clock(BASE))

    def test_check_timeout_only_after_deadline(self):
        with self.assertRaises(GuardFailed):
            transition(S.INTERVIEW_SCHEDULED, E.E_CHECK_TIMEOUT, ok(now_over_deadline=False, interview_revision=1, current_revision=1), P, Clock(BASE))
        r = transition(S.INTERVIEW_SCHEDULED, E.E_CHECK_TIMEOUT, ok(now_over_deadline=True, interview_revision=1, current_revision=1), P, Clock(BASE))
        self.assertEqual(r.to_state, S.NO_SHOW_PENDING)
        self.assertIn(("outbox", "Reschedule.Inquiry", {"once": True}), r.effects)
        self.assertIn(("job", "no_show_close_24h", {"entered_ms": BASE}), r.effects)

    def test_stale_revision_rejected(self):
        with self.assertRaises(TransitionRejected):
            transition(S.INTERVIEW_SCHEDULED, E.E_CHECKIN, ok(now_over_deadline=False, interview_revision=1, current_revision=2), P, Clock(BASE))


class TestNoShow(unittest.TestCase):
    def test_late_attendance_completes(self):
        r = transition(S.NO_SHOW_PENDING, E.E_CHECKIN, ok(late_attendance=True, hr_confirmed=True, interview_revision=1, current_revision=1), P, Clock(BASE))
        self.assertEqual(r.to_state, S.COMPLETED)

    def test_late_without_hr_confirm_fails(self):
        with self.assertRaises(GuardFailed):
            transition(S.NO_SHOW_PENDING, E.E_CHECKIN, ok(late_attendance=True, hr_confirmed=False), P, Clock(BASE))

    def test_reschedule_new_revision(self):
        r = transition(S.NO_SHOW_PENDING, E.E_RESCHEDULE,
                       ok(hr_confirmed=True, new_scheduled_at_ms=BASE + 2 * 3600000, current_revision=2, interview_revision=2), P, Clock(BASE))
        self.assertEqual(r.to_state, S.INTERVIEW_SCHEDULED)
        self.assertIn(("job", "no_show", {"scheduled_at_ms": BASE + 2 * 3600000, "revision": 2}), r.effects)

    def test_give_up_only_after_24h(self):
        with self.assertRaises(GuardFailed):
            transition(S.NO_SHOW_PENDING, E.E_GIVE_UP, ok(response_deadline_passed=False), P, Clock(BASE))
        r = transition(S.NO_SHOW_PENDING, E.E_GIVE_UP, ok(response_deadline_passed=True), P, Clock(BASE))
        self.assertEqual(r.to_state, S.CLOSED)


class TestWithdrawAndTerminal(unittest.TestCase):
    def test_withdraw_from_active_states(self):
        for st in (S.NEW, S.PENDING_CONFIRMATION, S.INTERVIEW_SCHEDULED, S.NO_SHOW_PENDING, S.REJECTED_QUALIFICATION):
            r = transition(st, E.E_WITHDRAW, ok(), P, Clock(BASE))
            self.assertEqual(r.to_state, S.CLOSED)
            # 已送出的不能假撤回：只取消未送達
            self.assertIn(("outbox", "Outbox.Cancel", {"scope": "undelivered_only"}), r.effects)

    def test_withdraw_from_terminal_rejected(self):
        for st in (S.REJECTED_BLACKLIST, S.LINE_ROUTED, S.COMPLETED, S.CLOSED):
            with self.assertRaises(TransitionRejected):
                transition(st, E.E_WITHDRAW, ok(), P, Clock(BASE))

    def test_terminal_rejects_new_events(self):
        for st in (S.REJECTED_BLACKLIST, S.LINE_ROUTED, S.COMPLETED, S.CLOSED):
            with self.assertRaises(TransitionRejected):
                transition(st, E.E_SCREEN, ok(), P, Clock(BASE))


class TestRejectionDue(unittest.TestCase):
    def test_72h_due_creates_draft_once_needs_approval(self):
        r = transition(S.REJECTED_QUALIFICATION, E.E_REJECTION_DUE, ok(), P, Clock(BASE + 72 * 3600000 + 1))
        self.assertEqual(r.to_state, S.REJECTED_QUALIFICATION)
        self.assertIn(("outbox", "Draft.Created", {"needs_hr_approval": True, "once": True}), r.effects)

    def test_rejected_qualification_not_terminal(self):
        self.assertNotIn(S.REJECTED_QUALIFICATION, {
            S.REJECTED_BLACKLIST, S.LINE_ROUTED, S.COMPLETED, S.CLOSED})


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_same_output(self):
        f = ok(blacklist_hit=False, data_complete=True, qualification_pass=False)
        a = transition(S.NEW, E.E_SCREEN, f, P, Clock(BASE))
        b = transition(S.NEW, E.E_SCREEN, f, P, Clock(BASE))
        self.assertEqual(a.to_state, b.to_state)
        self.assertEqual(a.effects, b.effects)
        self.assertEqual(a.audit, b.audit)


class TestU11Fixes(unittest.TestCase):
    """U1.1 回歸測試：GPAI 驗收（2026-11-01）擋下四缺陷的修復驗證。"""

    # 缺陷 1（打包）：zip 結構由 build 腳本保證（u1/... 層級），README 與 zip 逐項一致——見 U1_delivery_receipt.json v1.1。

    # 缺陷 2：合法時段 guard
    def test_schedule_in_past_rejected(self):
        with self.assertRaises(GuardFailed):
            transition(S.PENDING_CONFIRMATION, E.E_INTENT_ACCEPT,
                       ok(slots_available=True, region_mapped=True, hr_confirmed=True, scheduled_at_ms=BASE - 3600000, current_revision=1), P, Clock(BASE))

    def test_schedule_now_rejected(self):
        # 預約時間 == 現在 不算未來
        with self.assertRaises(GuardFailed):
            transition(S.PENDING_CONFIRMATION, E.E_INTENT_ACCEPT,
                       ok(slots_available=True, region_mapped=True, hr_confirmed=True, scheduled_at_ms=BASE, current_revision=1), P, Clock(BASE))

    def test_schedule_beyond_12h_window_rejected(self):
        with self.assertRaises(GuardFailed):
            transition(S.PENDING_CONFIRMATION, E.E_INTENT_ACCEPT,
                       ok(slots_available=True, region_mapped=True, hr_confirmed=True, scheduled_at_ms=BASE + 13 * 3600000, current_revision=1), P, Clock(BASE))

    def test_schedule_at_12h_upper_bound_ok(self):
        r = transition(S.PENDING_CONFIRMATION, E.E_INTENT_ACCEPT,
                       ok(slots_available=True, region_mapped=True, hr_confirmed=True, scheduled_at_ms=BASE + 12 * 3600000, current_revision=1), P, Clock(BASE))
        self.assertEqual(r.to_state, S.INTERVIEW_SCHEDULED)

    def test_reschedule_past_time_rejected(self):
        with self.assertRaises(GuardFailed):
            transition(S.INTERVIEW_SCHEDULED, E.E_RESCHEDULE,
                       ok(hr_confirmed=True, new_scheduled_at_ms=BASE - 1, current_revision=2, interview_revision=2), P, Clock(BASE))

    def test_reschedule_beyond_window_rejected(self):
        with self.assertRaises(GuardFailed):
            transition(S.INTERVIEW_SCHEDULED, E.E_RESCHEDULE,
                       ok(hr_confirmed=True, new_scheduled_at_ms=BASE + 13 * 3600000, current_revision=2, interview_revision=2), P, Clock(BASE))

    # 缺陷 3：revision 缺漏 fail-closed
    def test_checkin_missing_revision_fails_closed(self):
        with self.assertRaises(GuardFailed):
            transition(S.INTERVIEW_SCHEDULED, E.E_CHECKIN, ok(now_over_deadline=False), P, Clock(BASE))

    def test_check_timeout_missing_revision_fails_closed(self):
        with self.assertRaises(GuardFailed):
            transition(S.INTERVIEW_SCHEDULED, E.E_CHECK_TIMEOUT, ok(now_over_deadline=True), P, Clock(BASE))

    def test_reschedule_missing_revision_fails_closed(self):
        with self.assertRaises(GuardFailed):
            transition(S.INTERVIEW_SCHEDULED, E.E_RESCHEDULE,
                       ok(hr_confirmed=True, new_scheduled_at_ms=BASE + 3600000), P, Clock(BASE))

    # 缺陷 4：黑名單結果未知不放行
    def test_blacklist_unknown_stays_new_manual_check(self):
        r = transition(S.NEW, E.E_SCREEN, ok(blacklist_hit=None, data_complete=True, qualification_pass=True), P, Clock(BASE))
        self.assertEqual(r.to_state, S.NEW)
        self.assertIn(("mark", "blacklist_manual_check_required"), r.effects)


if __name__ == "__main__":
    unittest.main()
