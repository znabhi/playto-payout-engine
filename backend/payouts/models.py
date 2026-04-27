import uuid
import logging
from django.db import models
from django.db.models import Q, Sum
from django.utils.timezone import now
from datetime import timedelta

logger = logging.getLogger(__name__)


class Merchant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="bank_accounts"
    )
    account_number = models.CharField(max_length=20)
    ifsc = models.CharField(max_length=11)
    account_holder_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account_holder_name} – {self.account_number}"


class LedgerEntry(models.Model):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    ENTRY_TYPE_CHOICES = [(CREDIT, "Credit"), (DEBIT, "Debit")]

    HELD = "HELD"
    FINAL = "FINAL"
    STATUS_CHOICES = [(HELD, "Held"), (FINAL, "Final")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="ledger_entries"
    )
    entry_type = models.CharField(max_length=6, choices=ENTRY_TYPE_CHOICES)
    status = models.CharField(max_length=5, choices=STATUS_CHOICES)
    # Always store positive paise — direction is determined by entry_type
    amount_paise = models.BigIntegerField()
    # nullable FK — credits from customer payments have no payout reference
    reference = models.ForeignKey(
        "Payout",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ledger_entries",
    )
    description = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["merchant", "created_at"], name="ledger_merchant_date_idx"
            ),
            models.Index(
                fields=["merchant", "entry_type", "status"],
                name="ledger_balance_agg_idx",
            ),
            models.Index(
                fields=["reference", "status"], name="ledger_ref_status_idx"
            ),
        ]
        constraints = [
            # No zero or negative amounts — direction is entry_type
            models.CheckConstraint(
                check=Q(amount_paise__gt=0), name="amount_positive"
            ),
            # CREDIT is always FINAL; DEBIT can be HELD or FINAL
            models.CheckConstraint(
                check=(
                    (Q(entry_type="CREDIT") & Q(status="FINAL"))
                    | (Q(entry_type="DEBIT") & Q(status__in=["HELD", "FINAL"]))
                ),
                name="ledger_valid_type_status",
            ),
        ]

    def __str__(self):
        return f"{self.entry_type}/{self.status} {self.amount_paise}p – {self.merchant}"


class IdempotencyKey(models.Model):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    STATUS_CHOICES = [(PENDING, "Pending"), (COMPLETE, "Complete")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="idempotency_keys"
    )
    key = models.CharField(max_length=36)  # UUID string
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=PENDING)
    # Stores full HTTP response: {'status_code': 201, 'body': {...}}
    response_data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["merchant", "key"], name="unique_idem_per_merchant"
            )
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="idem_expires_at_idx"),
        ]

    def save(self, *args, **kwargs):
        # Safety: always ensure expires_at is set
        if not self.expires_at:
            self.expires_at = now() + timedelta(hours=24)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"IdemKey({self.key[:8]}…) – {self.merchant} [{self.status}]"


class InvalidTransition(Exception):
    """Raised when a Payout state machine transition is illegal."""
    pass


# Legal state transitions — anything not listed is illegal
LEGAL_TRANSITIONS = {
    "PENDING": ["PROCESSING"],
    "PROCESSING": ["COMPLETED", "FAILED"],
    "COMPLETED": [],  # terminal
    "FAILED": [],     # terminal
}


class Payout(models.Model):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="payouts"
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="payouts"
    )
    amount_paise = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    # OneToOneField enforces DB-level guarantee: one payout per idempotency key
    idempotency_key = models.OneToOneField(
        IdempotencyKey,
        on_delete=models.CASCADE,
        related_name="payout",
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Covers: filter(status='PROCESSING', last_attempted_at__lt=...)
            models.Index(
                fields=["status", "last_attempted_at"], name="payout_retry_idx"
            ),
        ]
        constraints = [
            # DB-level guard: only valid status strings persist
            models.CheckConstraint(
                check=Q(
                    status__in=["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
                ),
                name="payout_valid_status",
            ),
            # DB-level: one idempotency key = one payout, ever
            models.UniqueConstraint(
                fields=["merchant", "idempotency_key"],
                name="unique_payout_per_idem_key",
            ),
        ]

    def transition_to(self, new_status: str) -> None:
        """
        Enforce legal state transitions. Raises InvalidTransition for illegal moves.
        Always persists the change — callers must NOT call save() again for status.
        """
        if new_status not in LEGAL_TRANSITIONS.get(self.status, []):
            raise InvalidTransition(
                f"Payout {self.id}: {self.status} → {new_status} is not a legal transition"
            )
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])
        logger.info("Payout %s transitioned to %s", self.id, new_status)

    def __str__(self):
        return f"Payout({self.amount_paise}p, {self.status}) – {self.merchant}"
