"""
Test: Idempotency — same key returns identical response, no duplicate payout.
"""
import uuid
from django.test import TestCase, Client
from payouts.models import Merchant, BankAccount, LedgerEntry, Payout


class IdempotencyTest(TestCase):
    def setUp(self):
        self.merchant = Merchant.objects.create(
            name="Idem Test Merchant", email="idem@test.in"
        )
        self.bank = BankAccount.objects.create(
            merchant=self.merchant,
            account_number="1111111111",
            ifsc="SBIN0000001",
            account_holder_name="Idem User",
            is_active=True,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            status=LedgerEntry.FINAL,
            amount_paise=100_000,  # ₹1,000
            description="Seed credit",
        )
        self.client = Client()
        self.key = str(uuid.uuid4())

    def _post_payout(self):
        return self.client.post(
            "/api/v1/payouts/",
            data={
                "merchant_id": str(self.merchant.id),
                "bank_account_id": str(self.bank.id),
                "amount_paise": 5_000,  # ₹50
            },
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.key,
        )

    def test_same_key_returns_200_on_replay(self):
        r1 = self._post_payout()
        r2 = self._post_payout()

        self.assertEqual(r1.status_code, 201, f"First call should be 201, got {r1.status_code}")
        self.assertEqual(r2.status_code, 200, f"Replay should be 200, got {r2.status_code}")

    def test_body_is_identical_on_replay(self):
        r1 = self._post_payout()
        r2 = self._post_payout()
        self.assertEqual(r1.json(), r2.json(), "Replay response body must be byte-identical")

    def test_no_duplicate_payout_created(self):
        self._post_payout()
        self._post_payout()
        count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(count, 1, "Only one payout must exist for the same idempotency key")

    def test_different_keys_create_separate_payouts(self):
        self.client.post(
            "/api/v1/payouts/",
            data={
                "merchant_id": str(self.merchant.id),
                "bank_account_id": str(self.bank.id),
                "amount_paise": 5_000,
            },
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.client.post(
            "/api/v1/payouts/",
            data={
                "merchant_id": str(self.merchant.id),
                "bank_account_id": str(self.bank.id),
                "amount_paise": 5_000,
            },
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(count, 2, "Two different keys must create two payouts")
