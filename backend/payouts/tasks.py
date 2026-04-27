import random
import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils.timezone import now

from .models import LedgerEntry, Payout

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0, ignore_result=True)
def process_payout(self, payout_id):
    """
    Idempotent payout processor. Safe to re-run multiple times.
    Uses SELECT FOR UPDATE + PENDING status guard for at-most-once processing.
    """
    # ── Phase 1: Claim the payout atomically ────────────────────────────────
    with transaction.atomic():
        try:
            payout = Payout.objects.select_for_update().get(id=payout_id)
        except Payout.DoesNotExist:
            logger.error("process_payout: payout %s not found", payout_id)
            return

        if payout.status != Payout.PENDING:
            # Already claimed by another worker or completed — safe to exit
            logger.info(
                "process_payout: payout %s already in status %s, skipping",
                payout_id, payout.status,
            )
            return

        payout.transition_to(Payout.PROCESSING)
        payout.last_attempted_at = now()
        payout.save(update_fields=["last_attempted_at"])

    # ── Phase 2: Simulate bank settlement (outside lock — can be slow) ──────
    try:
        r = random.random()
        if r < 0.70:
            outcome = "success"
        elif r < 0.95:
            outcome = "fail"
        else:
            # Hang simulation (5% chance): return immediately leaving payout in
            # PROCESSING. retry_stuck_payouts beat task (runs every 15s) picks
            # it up after STUCK_THRESHOLD_SECONDS. DO NOT use time.sleep() —
            # that blocks a Celery worker thread and starves the pool.
            logger.info(
                "process_payout: payout %s simulating hang — leaving in PROCESSING",
                payout_id,
            )
            return
    except Exception:
        logger.exception("process_payout: unexpected error for payout %s", payout_id)
        return  # Leave in PROCESSING — retry scanner handles recovery

    # ── Phase 3: Settle atomically ───────────────────────────────────────────
    if outcome == "success":
        with transaction.atomic():
            payout = Payout.objects.select_for_update().get(id=payout_id)
            if payout.status != Payout.PROCESSING:
                return  # guard against double-settlement
            payout.transition_to(Payout.COMPLETED)
            # Finalise the HELD debit → funds permanently deducted
            LedgerEntry.objects.filter(
                reference=payout, status=LedgerEntry.HELD
            ).update(status=LedgerEntry.FINAL)
        logger.info("process_payout: payout %s COMPLETED", payout_id)

    else:  # outcome == "fail"
        with transaction.atomic():
            payout = Payout.objects.select_for_update().get(id=payout_id)
            if payout.status != Payout.PROCESSING:
                return
            payout.transition_to(Payout.FAILED)
            # Audit trail: finalise the HELD debit first …
            LedgerEntry.objects.filter(
                reference=payout, status=LedgerEntry.HELD
            ).update(status=LedgerEntry.FINAL)
            # … then offset with an equal CREDIT reversal → net zero
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.CREDIT,
                status=LedgerEntry.FINAL,
                amount_paise=payout.amount_paise,
                reference=payout,
                description=f"Reversal for failed payout {payout.id}",
            )
        logger.warning("process_payout: payout %s FAILED — funds reversed", payout_id)


# How long a payout can sit in PROCESSING before being retried.
# Low value = faster recovery for hung payouts.
# Beat runs every 15s, so effective worst-case detection = 15 + THRESHOLD.
STUCK_THRESHOLD_SECONDS = 10


@shared_task(ignore_result=True)
def retry_stuck_payouts():
    """
    Periodic beat task (every 15s). Finds payouts stuck in PROCESSING > 10s
    and re-queues them with exponential backoff. Max 3 attempts, then FAILED.

    Two-phase approach to minimise lock duration:
      Phase 1: short transaction to grab IDs under skip_locked
      Phase 2: process each payout individually in its own transaction
    """
    cutoff = now() - timedelta(seconds=STUCK_THRESHOLD_SECONDS)

    # Phase 1: Fetch IDs with a short-lived lock
    with transaction.atomic():
        payout_ids = list(
            Payout.objects
            .select_for_update(skip_locked=True)  # other workers skip these rows
            .filter(status=Payout.PROCESSING, last_attempted_at__lt=cutoff)
            .values_list("id", flat=True)
        )

    if not payout_ids:
        return

    # Phase 2: Process each individually — short locks, good parallelism
    for payout_id in payout_ids:
        with transaction.atomic():
            payout = (
                Payout.objects
                .select_for_update(skip_locked=True)
                .filter(id=payout_id, status=Payout.PROCESSING)
                .first()
            )
            if not payout:
                continue  # claimed by another worker between phase 1 and 2

            # Atomic DB-side increment — prevents lost-update if two workers
            # somehow see the same payout (defence-in-depth).
            Payout.objects.filter(id=payout.id).update(
                attempt_count=F("attempt_count") + 1,
                last_attempted_at=now(),
            )
            payout.refresh_from_db()  # reload for backoff calculation

            if payout.attempt_count < 3:
                delay = 2 ** (payout.attempt_count - 1)  # 1s, 2s, 4s
                logger.info(
                    "retry_stuck_payouts: retrying payout %s attempt=%d backoff=%ds",
                    payout.id, payout.attempt_count, delay,
                )
                process_payout.apply_async(args=[str(payout.id)], countdown=delay)
            else:
                # Max retries exceeded — fail and return funds atomically
                logger.warning(
                    "retry_stuck_payouts: payout %s exceeded max retries — failing",
                    payout.id,
                )
                payout.transition_to(Payout.FAILED)
                LedgerEntry.objects.filter(
                    reference=payout, status=LedgerEntry.HELD
                ).update(status=LedgerEntry.FINAL)
                LedgerEntry.objects.create(
                    merchant=payout.merchant,
                    entry_type=LedgerEntry.CREDIT,
                    status=LedgerEntry.FINAL,
                    amount_paise=payout.amount_paise,
                    reference=payout,
                    description="Reversal — max retries exceeded",
                )


@shared_task(ignore_result=True)
def cleanup_expired_idempotency_keys():
    """
    Hourly cleanup. Batched delete to avoid long table locks on large datasets.
    The expires_at index (idem_expires_at_idx) makes the filter query fast.
    """
    from .models import IdempotencyKey

    BATCH = 1000
    total = 0
    while True:
        ids = list(
            IdempotencyKey.objects
            .filter(expires_at__lt=now())
            .values_list("id", flat=True)[:BATCH]
        )
        if not ids:
            break
        deleted, _ = IdempotencyKey.objects.filter(id__in=ids).delete()
        total += deleted

    if total:
        logger.info("cleanup_expired_idempotency_keys: deleted %d expired keys", total)
