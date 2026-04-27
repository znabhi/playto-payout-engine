"""
Test: State machine — illegal transitions must be rejected.
"""
import uuid
from django.test import TestCase
from payouts.models import Merchant, BankAccount, LedgerEntry, IdempotencyKey, Payout, InvalidTransition


def _create_payout(status=Payout.PENDING):
    merchant = Merchant.objects.create(name="SM Merchant", email=f"sm{uuid.uuid4().hex[:6]}@test.in")
    bank = BankAccount.objects.create(
        merchant=merchant, account_number="0000000001",
        ifsc="TEST0000002", account_holder_name="SM User", is_active=True,
    )
    LedgerEntry.objects.create(
        merchant=merchant, entry_type=LedgerEntry.CREDIT,
        status=LedgerEntry.FINAL, amount_paise=100_000,
    )
    idem = IdempotencyKey.objects.create(merchant=merchant, key=str(uuid.uuid4()))
    payout = Payout.objects.create(
        merchant=merchant, bank_account=bank,
        amount_paise=5_000, status=status, idempotency_key=idem,
    )
    return payout


class StateMachineTest(TestCase):

    def test_pending_to_processing_is_legal(self):
        p = _create_payout(Payout.PENDING)
        p.transition_to(Payout.PROCESSING)
        p.refresh_from_db()
        self.assertEqual(p.status, Payout.PROCESSING)

    def test_processing_to_completed_is_legal(self):
        p = _create_payout(Payout.PROCESSING)
        p.transition_to(Payout.COMPLETED)
        p.refresh_from_db()
        self.assertEqual(p.status, Payout.COMPLETED)

    def test_processing_to_failed_is_legal(self):
        p = _create_payout(Payout.PROCESSING)
        p.transition_to(Payout.FAILED)
        p.refresh_from_db()
        self.assertEqual(p.status, Payout.FAILED)

    def test_completed_to_pending_is_illegal(self):
        p = _create_payout(Payout.COMPLETED)
        with self.assertRaises(InvalidTransition):
            p.transition_to(Payout.PENDING)

    def test_failed_to_completed_is_illegal(self):
        p = _create_payout(Payout.FAILED)
        with self.assertRaises(InvalidTransition):
            p.transition_to(Payout.COMPLETED)

    def test_pending_to_completed_is_illegal(self):
        p = _create_payout(Payout.PENDING)
        with self.assertRaises(InvalidTransition):
            p.transition_to(Payout.COMPLETED)

    def test_status_unchanged_after_illegal_transition(self):
        p = _create_payout(Payout.COMPLETED)
        try:
            p.transition_to(Payout.PENDING)
        except InvalidTransition:
            pass
        p.refresh_from_db()
        self.assertEqual(p.status, Payout.COMPLETED)
