import uuid
import json
import logging
from datetime import timedelta

from django.db import transaction, IntegrityError
from django.db.models import Q, Sum, F
from django.utils.timezone import now

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from .models import (
    Merchant,
    BankAccount,
    LedgerEntry,
    IdempotencyKey,
    Payout,
)
from .serializers import (
    MerchantSerializer,
    BankAccountSerializer,
    LedgerEntrySerializer,
    PayoutSerializer,
)
from .exceptions import InsufficientFundsError

logger = logging.getLogger(__name__)

MAX_PAYOUT_LIMIT_PAISE = 10_000_000  # 1 lakh INR


# ─── Merchants ──────────────────────────────────────────────────────────────

@api_view(["GET"])
def merchant_list(request):
    """List all merchants (used by frontend merchant selector)."""
    merchants = Merchant.objects.all()
    return Response(MerchantSerializer(merchants, many=True).data)


@api_view(["GET"])
def merchant_balance(request, merchant_id):
    """
    Derive balance from the ledger using a single DB aggregation.
    No stored balance column — prevents stale-read bugs.

    Formula:
      available = credits - final_debits - held_debits
      held      = held_debits  (already part of available deduction, shown separately for UX)
    Invariant:  available + held == credits - final_debits
    """
    try:
        merchant = Merchant.objects.get(pk=merchant_id)
    except Merchant.DoesNotExist:
        return Response({"detail": "Merchant not found."}, status=404)

    agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(
        credits=Sum("amount_paise", filter=Q(entry_type="CREDIT")),
        final_debits=Sum(
            "amount_paise", filter=Q(entry_type="DEBIT", status="FINAL")
        ),
        held_debits=Sum(
            "amount_paise", filter=Q(entry_type="DEBIT", status="HELD")
        ),
        reversal_credits=Sum(
            "amount_paise",
            # Structurally correct: all payout-linked CREDITs are reversals.
            # Avoids fragile description string matching.
            filter=Q(entry_type="CREDIT", reference__isnull=False)
        ),
    )
    # Null-safe: Sum returns None when no matching rows exist
    credits          = agg["credits"]          or 0
    final_debits     = agg["final_debits"]     or 0
    held_debits      = agg["held_debits"]      or 0
    reversal_credits = agg["reversal_credits"] or 0
    available        = credits - final_debits - held_debits
    # Net settled = only COMPLETED payouts (gross debits minus reversals)
    net_settled      = final_debits - reversal_credits

    return Response(
        {
            "merchant_id": str(merchant_id),
            "available_paise": available,
            "held_paise": held_debits,
            "total_credited_paise": credits,
            "total_debited_paise": final_debits + held_debits,  # gross (audit trail)
            "net_settled_paise": net_settled,                   # only completed payouts
        }
    )


@api_view(["GET"])
def merchant_ledger(request, merchant_id):
    """Paginated ledger entries for a merchant, newest first."""
    try:
        merchant = Merchant.objects.get(pk=merchant_id)
    except Merchant.DoesNotExist:
        return Response({"detail": "Merchant not found."}, status=404)

    entries = LedgerEntry.objects.filter(merchant=merchant).select_related("reference")
    paginator = PageNumberPagination()
    paginator.page_size = 20
    page = paginator.paginate_queryset(entries, request)
    return paginator.get_paginated_response(LedgerEntrySerializer(page, many=True).data)


@api_view(["GET"])
def merchant_bank_accounts(request, merchant_id):
    """List active bank accounts for a merchant."""
    try:
        merchant = Merchant.objects.get(pk=merchant_id)
    except Merchant.DoesNotExist:
        return Response({"detail": "Merchant not found."}, status=404)
    accounts = BankAccount.objects.filter(merchant=merchant, is_active=True)
    return Response(BankAccountSerializer(accounts, many=True).data)


# ─── Payouts ────────────────────────────────────────────────────────────────

@api_view(["POST"])
def create_payout(request):
    """
    Create a payout with full idempotency and concurrency protection.

    Lock order (prevents deadlock):  Merchant → IdempotencyKey → Payout
    All writes happen inside ONE transaction.atomic() block.
    Celery task is enqueued AFTER commit via on_commit().
    """
    # ── 1. Pre-validation (before taking any locks) ─────────────────────────
    idem_key_str = request.headers.get("Idempotency-Key", "").strip()
    if not idem_key_str:
        return Response(
            {"detail": "Idempotency-Key header is required."}, status=400
        )
    try:
        uuid.UUID(idem_key_str, version=4)
    except ValueError:
        return Response(
            {"detail": "Idempotency-Key must be a valid UUID v4."}, status=400
        )

    amount_paise = request.data.get("amount_paise")
    bank_account_id = request.data.get("bank_account_id")
    merchant_id = request.data.get("merchant_id")

    if not amount_paise or not bank_account_id or not merchant_id:
        return Response(
            {"detail": "merchant_id, amount_paise, and bank_account_id are required."},
            status=400,
        )

    try:
        amount_paise = int(amount_paise)
    except (TypeError, ValueError):
        return Response({"detail": "amount_paise must be an integer."}, status=400)

    if not (0 < amount_paise <= MAX_PAYOUT_LIMIT_PAISE):
        return Response(
            {"detail": f"amount_paise must be between 1 and {MAX_PAYOUT_LIMIT_PAISE}."},
            status=400,
        )

    # Fetch merchant + bank account outside transaction for basic validation
    try:
        merchant = Merchant.objects.get(pk=merchant_id)
    except (Merchant.DoesNotExist, Exception):
        return Response({"detail": "Merchant not found."}, status=404)

    try:
        bank_account = BankAccount.objects.get(pk=bank_account_id)
    except BankAccount.DoesNotExist:
        return Response({"detail": "Bank account not found."}, status=404)

    if str(bank_account.merchant_id) != str(merchant_id):
        return Response(
            {"detail": "Bank account does not belong to this merchant."}, status=403
        )

    if not bank_account.is_active:
        return Response({"detail": "Bank account is inactive."}, status=400)

    # ── 2. Atomic block: lock → idempotency → balance → create ──────────────
    payout_to_enqueue = None

    with transaction.atomic():
        # Lock order Step 1: Lock Merchant FIRST.
        # nowait=False (blocking) is intentional — a money operation must wait,
        # not fail-fast. Second concurrent request waits here until first commits.
        merchant = Merchant.objects.select_for_update().get(pk=merchant_id)

        # Lock order Step 2: Idempotency gate — AFTER merchant is locked.
        # Nested atomic() = savepoint: on IntegrityError, only the savepoint
        # rolls back, leaving the outer transaction alive. Works on both
        # PostgreSQL and SQLite (unlike bare try/except IntegrityError).
        idem_key = None
        existing_idem = None
        try:
            with transaction.atomic():
                idem_key = IdempotencyKey.objects.create(
                    merchant=merchant,
                    key=idem_key_str,
                    status=IdempotencyKey.PENDING,
                    expires_at=now() + timedelta(hours=24),
                )
        except IntegrityError:
            # Key exists — lock the row before reading
            existing_idem = (
                IdempotencyKey.objects
                .select_for_update()
                .filter(merchant=merchant, key=idem_key_str)
                .first()  # .filter().first() avoids DoesNotExist if row deleted mid-request
            )

        if existing_idem is not None:
            if existing_idem.expires_at < now():
                # Expired — delete and create fresh
                existing_idem.delete()
                idem_key = IdempotencyKey.objects.create(
                    merchant=merchant, key=idem_key_str,
                    status=IdempotencyKey.PENDING,
                    expires_at=now() + timedelta(hours=24),
                )
            elif existing_idem.status == IdempotencyKey.COMPLETE:
                # Byte-identical replay — 200 indicates "idempotency replay",
                # body is identical to the first response.
                return Response(
                    existing_idem.response_data["body"],
                    status=200,
                )
            else:
                return Response({"detail": "Request in flight."}, status=202)
        elif idem_key is None:
            # Row deleted between create fail and fetch (rare cleanup race) — re-create
            idem_key = IdempotencyKey.objects.create(
                merchant=merchant, key=idem_key_str,
                status=IdempotencyKey.PENDING,
                expires_at=now() + timedelta(hours=24),
            )

        # Lock order Step 3: balance check — merchant already locked in Step 1.
        agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(
            credits=Sum("amount_paise", filter=Q(entry_type="CREDIT")),
            final_debits=Sum(
                "amount_paise", filter=Q(entry_type="DEBIT", status="FINAL")
            ),
            held_debits=Sum(
                "amount_paise", filter=Q(entry_type="DEBIT", status="HELD")
            ),
        )
        credits      = agg["credits"]      or 0
        final_debits = agg["final_debits"] or 0
        held_debits  = agg["held_debits"]  or 0
        available    = credits - final_debits - held_debits

        if available < amount_paise:
            logger.warning(
                "Insufficient funds: merchant=%s available=%d requested=%d",
                merchant_id, available, amount_paise,
            )
            raise InsufficientFundsError()
            # IntegrityError on idem_key is rolled back — no orphaned PENDING key

        # Create payout + HELD debit atomically
        payout = Payout.objects.create(
            merchant=merchant,
            bank_account=bank_account,
            amount_paise=amount_paise,
            status=Payout.PENDING,
            idempotency_key=idem_key,
        )
        LedgerEntry.objects.create(
            merchant=merchant,
            entry_type=LedgerEntry.DEBIT,
            status=LedgerEntry.HELD,
            amount_paise=amount_paise,
            reference=payout,
            description=f"Hold for payout {payout.id}",
        )

        # Seal idempotency key — COMPLETE only after ALL writes succeed
        # Convert to JSON-safe dict: UUIDs → str, datetimes → ISO strings
        raw_data = PayoutSerializer(payout).data
        json_safe_body = json.loads(JSONRenderer().render(raw_data))
        idem_key.response_data = {"status_code": 201, "body": json_safe_body}
        idem_key.status = IdempotencyKey.COMPLETE
        idem_key.save()

        logger.info(
            "Payout created: payout=%s merchant=%s amount=%d",
            payout.id, merchant_id, amount_paise,
        )
        payout_to_enqueue = payout

    # ── 3. Enqueue AFTER commit — worker always sees committed payout row ────
    if payout_to_enqueue:
        transaction.on_commit(
            lambda: _enqueue_payout(payout_to_enqueue.id)
        )

    return Response(json_safe_body, status=201)


def _enqueue_payout(payout_id):
    from .tasks import process_payout
    process_payout.delay(payout_id)


@api_view(["GET"])
def payout_list(request):
    """List payouts filtered by ?merchant_id=, newest first."""
    merchant_id = request.query_params.get("merchant_id")
    qs = Payout.objects.select_related("bank_account", "merchant")
    if merchant_id:
        qs = qs.filter(merchant_id=merchant_id)
    paginator = PageNumberPagination()
    paginator.page_size = 20
    page = paginator.paginate_queryset(qs, request)
    return paginator.get_paginated_response(PayoutSerializer(page, many=True).data)


@api_view(["GET"])
def payout_detail(request, payout_id):
    """Single payout status."""
    try:
        payout = Payout.objects.select_related("bank_account", "merchant").get(
            pk=payout_id
        )
    except Payout.DoesNotExist:
        return Response({"detail": "Payout not found."}, status=404)
    return Response(PayoutSerializer(payout).data)
