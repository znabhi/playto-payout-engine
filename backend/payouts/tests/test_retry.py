"""
Test: Retry logic — payouts stuck in PROCESSING get retried with backoff.
Max 3 attempts → FAILED with fund reversal.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils.timezone import now
from payouts.models import Merchant, BankAccount, LedgerEntry, IdempotencyKey, Payout
from payouts.tasks import retry_stuck_payouts


def _make_stuck_payout(attempt_count=0):
    merchant = Merchant.objects.create(
        name=f"Retry Merchant {uuid.uuid4().hex[:4]}", email=f"retry{uuid.uuid4().hex[:6]}@test.in"
    )
    bank = BankAccount.objects.create(
        merchant=merchant, account_number="7777777777",
        ifsc="TEST0000004", account_holder_name="Retry User", is_active=True,
    )
    LedgerEntry.objects.create(
        merchant=merchant, entry_type=LedgerEntry.CREDIT,
        status=LedgerEntry.FINAL, amount_paise=500_000,
    )
    idem = IdempotencyKey.objects.create(merchant=merchant, key=str(uuid.uuid4()))
    payout = Payout.objects.create(
        merchant=merchant, bank_account=bank,
        amount_paise=10_000, status=Payout.PROCESSING,
        idempotency_key=idem,
        attempt_count=attempt_count,
        last_attempted_at=now() - timedelta(seconds=60),  # stuck for 60s > 30s threshold
    )
    LedgerEntry.objects.create(
        merchant=merchant, entry_type=LedgerEntry.DEBIT,
        status=LedgerEntry.HELD, amount_paise=10_000, reference=payout,
    )
    return payout


class RetryTest(TestCase):

    @patch("payouts.tasks.process_payout.apply_async")
    def test_stuck_payout_gets_retried(self, mock_apply_async):
        payout = _make_stuck_payout(attempt_count=0)
        retry_stuck_payouts()
        payout.refresh_from_db()
        self.assertEqual(payout.attempt_count, 1)
        mock_apply_async.assert_called_once()

    @patch("payouts.tasks.process_payout.apply_async")
    def test_backoff_increases_with_attempt_count(self, mock_apply_async):
        payout = _make_stuck_payout(attempt_count=1)
        retry_stuck_payouts()
        payout.refresh_from_db()
        # attempt_count goes from 1 → 2, backoff = 2**2 = 4s
        call_kwargs = mock_apply_async.call_args
        self.assertEqual(call_kwargs.kwargs["countdown"], 4)

    def test_max_retries_exceeded_marks_failed(self):
        payout = _make_stuck_payout(attempt_count=3)  # already at max
        retry_stuck_payouts()
        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.FAILED)

    def test_max_retries_creates_credit_reversal(self):
        payout = _make_stuck_payout(attempt_count=3)
        retry_stuck_payouts()
        reversal = LedgerEntry.objects.filter(
            reference=payout, entry_type=LedgerEntry.CREDIT, status=LedgerEntry.FINAL
        )
        self.assertEqual(reversal.count(), 1)
        self.assertEqual(reversal.first().amount_paise, 10_000)

    def test_non_stuck_payouts_not_retried(self):
        """Payouts stuck less than 30s must be left alone."""
        merchant = Merchant.objects.create(
            name="Fresh Merchant", email=f"fresh{uuid.uuid4().hex[:6]}@test.in"
        )
        bank = BankAccount.objects.create(
            merchant=merchant, account_number="8888888888",
            ifsc="TEST0000005", account_holder_name="Fresh User", is_active=True,
        )
        idem = IdempotencyKey.objects.create(merchant=merchant, key=str(uuid.uuid4()))
        fresh_payout = Payout.objects.create(
            merchant=merchant, bank_account=bank, amount_paise=5_000,
            status=Payout.PROCESSING, idempotency_key=idem,
            last_attempted_at=now() - timedelta(seconds=5),  # only 5s old — not stuck
        )
        with patch("payouts.tasks.process_payout.apply_async") as mock_enqueue:
            retry_stuck_payouts()
        mock_enqueue.assert_not_called()
        fresh_payout.refresh_from_db()
        self.assertEqual(fresh_payout.status, Payout.PROCESSING)
