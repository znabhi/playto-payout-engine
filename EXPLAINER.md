# EXPLAINER.md — Playto Payout Engine

## 1. The Ledger

### Balance calculation query

```python
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
```

### Why this model?

No stored `balance` column — a stored balance is a cache that can go stale, especially under concurrent writes. Balance is derived from the ledger in a single SQL aggregation, so it is always correct by construction.

Amounts in **paise (integer)** — no floats, no decimals, no rounding bugs. Float arithmetic on money is a class of bugs that should not exist.

Two debit statuses: **HELD** and **FINAL**.
- HELD = funds reserved for a payout in progress, not yet settled with the bank. The merchant cannot spend them.
- FINAL = settlement confirmed (success) or audit record of a failed attempt (before reversal).

This distinction lets `available` correctly exclude both settled debits and in-flight reservations. A single-status debit model would either block legitimate payouts (treating all debits as permanent) or allow overdrafts (ignoring in-progress holds).

Invariant: `available + held == credits - final_debits` — there are unit tests that verify this holds at every lifecycle stage.

---

## 2. The Lock

### Exact code

```python
with transaction.atomic():
    # Lock ordering: Merchant → IdempotencyKey → Payout (consistent everywhere)
    # nowait=False (blocking) is intentional — second concurrent request waits
    # for the first to commit before it can read the balance.
    merchant = Merchant.objects.select_for_update().get(pk=merchant_id)

    # ... idempotency gate ...

    agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(...)
    available = credits - final_debits - held_debits
    if available < amount_paise:
        raise InsufficientFundsError()

    payout = Payout.objects.create(...)
    LedgerEntry.objects.create(entry_type="DEBIT", status="HELD", ...)
```

### What database primitive it relies on

`SELECT ... FOR UPDATE` — a PostgreSQL row-level exclusive lock. When the second concurrent request reaches this line, it blocks at the database level until the first transaction commits. By then, the first transaction has written the HELD debit, so the second sees reduced available balance and raises `InsufficientFundsError`.

This is critical: Python-level locks (`threading.Lock`, etc.) are process-scoped and completely useless across multiple Gunicorn/Celery workers. Only database primitives guarantee correctness in a multi-process deployment.

---

## 3. The Idempotency

### How the system knows it has seen a key before

On every payout request, we attempt `IdempotencyKey.objects.create(merchant, key, status=PENDING, ...)` inside a `transaction.atomic()`. PostgreSQL enforces a `UNIQUE(merchant, key)` constraint. If the key already exists, it raises `IntegrityError`.

We catch the `IntegrityError` and fetch the existing row under `select_for_update()` to read its status:
- `COMPLETE` → return the cached `response_data` (full HTTP status + body). This is byte-identical to the first response.
- `PENDING` → the first request is still in flight → return 202.
- Expired → delete and treat as a new request.
- Row deleted between create fail and fetch (extremely rare cleanup-task race) → re-create safely.

### What happens if the first request is in flight when the second arrives

The first request created the `IdempotencyKey` in `PENDING` status. The `COMPLETE` seal only happens after all writes succeed within the same transaction. So the second request finds `PENDING` and returns `202 Request in flight` — no duplicate payout is created.

Two concurrent requests that both see the key as new: both attempt `create()`, one fails with `IntegrityError`, then tries `select_for_update()` which blocks until the first transaction commits. The second then reads the committed state and deduplicates correctly.

---

## 4. The State Machine

### Where FAILED → COMPLETED is blocked

```python
# In payouts/models.py

LEGAL_TRANSITIONS = {
    "PENDING": ["PROCESSING"],
    "PROCESSING": ["COMPLETED", "FAILED"],
    "COMPLETED": [],  # terminal — empty list means nothing allowed
    "FAILED": [],     # terminal
}

def transition_to(self, new_status: str) -> None:
    if new_status not in LEGAL_TRANSITIONS.get(self.status, []):
        raise InvalidTransition(
            f"Payout {self.id}: {self.status} → {new_status} is not a legal transition"
        )
    self.status = new_status
    self.save(update_fields=["status", "updated_at"])
```

`LEGAL_TRANSITIONS["FAILED"]` is an empty list. The check `if new_status not in []` is always `True`, so `InvalidTransition` is always raised for any transition from FAILED — including FAILED → COMPLETED.

**Important:** The DB `CheckConstraint(check=Q(status__in=["PENDING","PROCESSING","COMPLETED","FAILED"]))` validates that only legal status strings exist, but PostgreSQL cannot enforce transition ordering without triggers. Transition correctness is solely the responsibility of `transition_to()`. Every settlement path in `tasks.py` uses `transition_to()` — there is no raw `.status =` assignment anywhere.

---

## 5. The AI Audit

### What AI initially gave me (wrong code)

When I asked for the balance calculation, AI generated:

```python
# What AI gave
entries = LedgerEntry.objects.filter(merchant=merchant)
credits = sum(e.amount_paise for e in entries if e.entry_type == "CREDIT")
debits  = sum(e.amount_paise for e in entries if e.entry_type == "DEBIT")
balance = credits - debits
```

### Why this is wrong

Three bugs:

1. **Python-level arithmetic on fetched rows** — fetches every row into memory, then sums in Python. On a merchant with 10,000 ledger entries, this is an OOM risk and 100× slower than a DB aggregation.

2. **No HELD/FINAL distinction** — all debits treated identically. A HELD debit that gets a reversal CREDIT later would still subtract from balance even after the reversal, showing incorrect (lower) available balance.

3. **TOCTOU race** — reading rows, summing in Python, then checking balance is not atomic. Another request can write a ledger entry between the read and the check, causing overdrafts.

### What I replaced it with

```python
# What I shipped
agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(
    credits=Sum("amount_paise", filter=Q(entry_type="CREDIT")),
    final_debits=Sum("amount_paise", filter=Q(entry_type="DEBIT", status="FINAL")),
    held_debits=Sum("amount_paise", filter=Q(entry_type="DEBIT", status="HELD")),
)
credits      = agg["credits"]      or 0
final_debits = agg["final_debits"] or 0
held_debits  = agg["held_debits"]  or 0
available    = credits - final_debits - held_debits
```

Single SQL query with `SUM` filters — efficient, correct, and atomic within the surrounding transaction. The `or 0` handles the `None` case when no matching rows exist (Django returns `None` from `Sum` on an empty queryset).
