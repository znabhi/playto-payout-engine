"""
Test: Held funds lifecycle — HELD on creation, FINAL on success, reversed on failure.
"""
import uuid
from django.test import TestCase
from payouts.models import Merchant, BankAccount, LedgerEntry, IdempotencyKey, Payout
from payouts.tasks import process_payout
from unittest.mock import patch


def _make_merchant_with_bank():
    merchant = Merchant.objects.create(
        name="Lifecycle Merchant", email=f"lc{uuid.uuid4().hex[:6]}@test.in"
    )
    bank = BankAccount.objects.create(
        merchant=merchant, account_number="5555555555",
        ifsc="TEST0000003", account_holder_name="LC User", is_active=True,
    )
    LedgerEntry.objects.create(
        merchant=merchant, entry_type=LedgerEntry.CREDIT,
        status=LedgerEntry.FINAL, amount_paise=1_000_000,
    )
    return merchant, bank


def _create_payout_with_held(merchant, bank, amount=50_000):
    idem = IdempotencyKey.objects.create(merchant=merchant, key=str(uuid.uuid4()))
    payout = Payout.objects.create(
        merchant=merchant, bank_account=bank,
        amount_paise=amount, status=Payout.PENDING, idempotency_key=idem,
    )
    LedgerEntry.objects.create(
        merchant=merchant, entry_type=LedgerEntry.DEBIT,
        status=LedgerEntry.HELD, amount_paise=amount, reference=payout,
    )
    return payout


class HeldLifecycleTest(TestCase):

    def test_held_entry_created_on_payout(self):
        merchant, bank = _make_merchant_with_bank()
        payout = _create_payout_with_held(merchant, bank)
        held = LedgerEntry.objects.filter(reference=payout, status=LedgerEntry.HELD)
        self.assertEqual(held.count(), 1)

    def test_success_finalises_held_entry(self):
        merchant, bank = _make_merchant_with_bank()
        payout = _create_payout_with_held(merchant, bank)

        with patch("random.random", return_value=0.5):  # force success path
            process_payout(str(payout.id))

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.COMPLETED)
        held_count = LedgerEntry.objects.filter(reference=payout, status=LedgerEntry.HELD).count()
        self.assertEqual(held_count, 0)
        final_count = LedgerEntry.objects.filter(reference=payout, status=LedgerEntry.FINAL, entry_type=LedgerEntry.DEBIT).count()
        self.assertEqual(final_count, 1)

    def test_failure_reverses_funds_atomically(self):
        merchant, bank = _make_merchant_with_bank()
        payout = _create_payout_with_held(merchant, bank, amount=50_000)

        with patch("random.random", return_value=0.85):  # force fail path
            process_payout(str(payout.id))

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.FAILED)

        # HELD was finalised
        held_count = LedgerEntry.objects.filter(reference=payout, status=LedgerEntry.HELD).count()
        self.assertEqual(held_count, 0)

        # Equal CREDIT reversal was created
        reversal = LedgerEntry.objects.filter(
            reference=payout, entry_type=LedgerEntry.CREDIT, status=LedgerEntry.FINAL
        )
        self.assertEqual(reversal.count(), 1)
        self.assertEqual(reversal.first().amount_paise, 50_000)

    def test_failure_net_zero_on_ledger(self):
        """DEBIT FINAL + CREDIT FINAL for same payout = net zero impact."""
        merchant, bank = _make_merchant_with_bank()
        initial_credits = 1_000_000

        payout = _create_payout_with_held(merchant, bank, amount=50_000)
        with patch("random.random", return_value=0.85):
            process_payout(str(payout.id))

        from django.db.models import Q, Sum
        agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(
            credits=Sum("amount_paise", filter=Q(entry_type=LedgerEntry.CREDIT)),
            debits=Sum("amount_paise", filter=Q(entry_type=LedgerEntry.DEBIT, status=LedgerEntry.FINAL)),
        )
        # Net balance should equal initial seed (reversal cancelled the debit)
        net = (agg["credits"] or 0) - (agg["debits"] or 0)
        self.assertEqual(net, initial_credits)
