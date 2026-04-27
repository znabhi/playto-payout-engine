"""
Test: Balance invariant — available + held == credits - final_debits for every merchant.
"""
import uuid
from django.test import TestCase
from django.db.models import Q, Sum
from payouts.models import Merchant, BankAccount, LedgerEntry, IdempotencyKey, Payout
from payouts.tasks import process_payout
from unittest.mock import patch


def _compute_balance(merchant):
    agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(
        credits=Sum("amount_paise", filter=Q(entry_type="CREDIT")),
        final_debits=Sum("amount_paise", filter=Q(entry_type="DEBIT", status="FINAL")),
        held_debits=Sum("amount_paise", filter=Q(entry_type="DEBIT", status="HELD")),
    )
    credits      = agg["credits"]      or 0
    final_debits = agg["final_debits"] or 0
    held_debits  = agg["held_debits"]  or 0
    available    = credits - final_debits - held_debits
    held         = held_debits
    return credits, final_debits, held_debits, available, held


class BalanceInvariantTest(TestCase):

    def setUp(self):
        self.merchant = Merchant.objects.create(
            name="Invariant Merchant", email="invariant@test.in"
        )
        self.bank = BankAccount.objects.create(
            merchant=self.merchant, account_number="3333333333",
            ifsc="TEST0000006", account_holder_name="Inv User", is_active=True,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.CREDIT,
            status=LedgerEntry.FINAL, amount_paise=500_000,
        )

    def _assert_invariant(self):
        credits, final_debits, held_debits, available, held = _compute_balance(self.merchant)
        self.assertEqual(
            available + held, credits - final_debits,
            f"Invariant broken: available({available}) + held({held}) "
            f"!= credits({credits}) - final_debits({final_debits})"
        )

    def test_invariant_holds_after_credit(self):
        self._assert_invariant()

    def test_invariant_holds_after_payout_created(self):
        idem = IdempotencyKey.objects.create(merchant=self.merchant, key=str(uuid.uuid4()))
        payout = Payout.objects.create(
            merchant=self.merchant, bank_account=self.bank,
            amount_paise=50_000, idempotency_key=idem,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.DEBIT,
            status=LedgerEntry.HELD, amount_paise=50_000, reference=payout,
        )
        self._assert_invariant()

    def test_invariant_holds_after_success(self):
        idem = IdempotencyKey.objects.create(merchant=self.merchant, key=str(uuid.uuid4()))
        payout = Payout.objects.create(
            merchant=self.merchant, bank_account=self.bank,
            amount_paise=50_000, idempotency_key=idem,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.DEBIT,
            status=LedgerEntry.HELD, amount_paise=50_000, reference=payout,
        )
        with patch("random.random", return_value=0.5):
            process_payout(str(payout.id))
        self._assert_invariant()

    def test_invariant_holds_after_failure(self):
        idem = IdempotencyKey.objects.create(merchant=self.merchant, key=str(uuid.uuid4()))
        payout = Payout.objects.create(
            merchant=self.merchant, bank_account=self.bank,
            amount_paise=50_000, idempotency_key=idem,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.DEBIT,
            status=LedgerEntry.HELD, amount_paise=50_000, reference=payout,
        )
        with patch("random.random", return_value=0.85):
            process_payout(str(payout.id))
        self._assert_invariant()
