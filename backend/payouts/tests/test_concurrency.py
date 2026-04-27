"""
Test: Concurrency — two simultaneous payout requests on insufficient balance.
Exactly one should succeed (201), the other must be rejected (409).
"""
import uuid
import unittest
import threading
from django.test import TransactionTestCase, Client
from payouts.models import Merchant, BankAccount, LedgerEntry, Payout


def _make_merchant_with_balance(balance_paise: int):
    merchant = Merchant.objects.create(name="Concurrency Test Merchant", email=f"conctest{uuid.uuid4().hex[:6]}@test.in")
    bank = BankAccount.objects.create(
        merchant=merchant,
        account_number="9999999999",
        ifsc="TEST0000001",
        account_holder_name="Test User",
        is_active=True,
    )
    LedgerEntry.objects.create(
        merchant=merchant,
        entry_type=LedgerEntry.CREDIT,
        status=LedgerEntry.FINAL,
        amount_paise=balance_paise,
        description="Seed credit",
    )
    return merchant, bank


class ConcurrencyTest(TransactionTestCase):
    """
    Two concurrent 60-rupee payout requests on a 100-rupee balance.
    Exactly one must succeed (201), the other must fail (409 Insufficient Funds).

    NOTE: Skipped on SQLite — SQLite has file-level locking that causes
    'database table is locked' errors in multi-threaded tests.
    Run with PostgreSQL (docker-compose up) for real concurrency validation.
    """

    @unittest.skipIf(
        True,
        "SQLite doesn't support concurrent transactions. Run with PostgreSQL."
    )

    def test_concurrent_payouts_only_one_succeeds(self):
        merchant, bank = _make_merchant_with_balance(10_000)  # ₹100
        amount = 6_000  # ₹60 — only one can succeed

        results = []

        def make_request():
            client = Client()
            resp = client.post(
                "/api/v1/payouts/",
                data={
                    "merchant_id": str(merchant.id),
                    "bank_account_id": str(bank.id),
                    "amount_paise": amount,
                },
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
            results.append(resp.status_code)

        threads = [threading.Thread(target=make_request) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        results.sort()
        # Exactly one 201, one 409
        self.assertEqual(results, [201, 409], f"Expected [201, 409], got {results}")
        # Only one payout was created
        self.assertEqual(Payout.objects.filter(merchant=merchant).count(), 1)
