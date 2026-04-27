# Playto Payout Engine

Production-grade payout engine for Playto Pay. Built with Django + DRF + PostgreSQL + Celery + React.

## 🎥 Demo Video

https://github.com/znabhi/playto-payout-engine/raw/main/Playto%20Pay%20%E2%80%94%20Merchant%20Dashboard.mp4

> Also watch on Loom: [Watch the full demo →](https://www.loom.com/share/81236e5a234b4c3e92e3c1845adecc12)

## Quick Start (Docker)

```bash
git clone https://github.com/znabhi/playto-payout-engine.git
cd playto-payout-engine
docker-compose up --build
```

Then in a second terminal:
```bash
docker-compose exec backend python manage.py migrate
docker-compose exec backend python manage.py createsuperuser
docker-compose exec backend python manage.py seed_data
```

Visit:
- **Frontend**: http://localhost:5173
- **API**: http://localhost:8000/api/v1/
- **Admin**: http://localhost:8000/admin/

## Local Dev (without Docker)

```bash
# Backend
cd backend
pip install -r requirements.txt
# Set DATABASE_URL and CELERY_BROKER_URL in .env
python manage.py migrate
python manage.py seed_data
python manage.py runserver

# Celery worker (separate terminal)
celery -A config worker --loglevel=info

# Celery beat (separate terminal)
celery -A config beat --loglevel=info

# Frontend (separate terminal)
cd ../frontend
npm install
npm run dev
```

## Run Tests

```bash
cd backend
pytest payouts/tests/ -v
```

## Verify Balance Invariant

```bash
cd backend
python manage.py check_invariant
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/merchants/` | List all merchants |
| GET | `/api/v1/merchants/{id}/balance/` | Merchant balance (available + held) |
| GET | `/api/v1/merchants/{id}/ledger/` | Paginated ledger entries |
| GET | `/api/v1/merchants/{id}/bank-accounts/` | Merchant bank accounts |
| POST | `/api/v1/payouts/` | Create payout (requires `Idempotency-Key` header) |
| GET | `/api/v1/payouts/list/` | List payouts (`?merchant_id=`) |
| GET | `/api/v1/payouts/{id}/` | Single payout status |

### Create Payout

```http
POST /api/v1/payouts/
Idempotency-Key: <uuid-v4>
Content-Type: application/json

{
  "merchant_id": "<uuid>",
  "bank_account_id": "<uuid>",
  "amount_paise": 50000
}
```

**Response codes:**
- `201` — Payout created
- `200` — Idempotency replay (same response as first call)
- `202` — First request still in-flight
- `409` — Insufficient funds
- `400` — Validation error
- `403` — Bank account not owned by merchant

## Architecture

- **No stored balance** — derived from ledger aggregation with a single DB query
- **Concurrency** — `SELECT FOR UPDATE` on merchant row prevents double-spend
- **Idempotency** — `UniqueConstraint` on `(merchant, key)` + atomic `IntegrityError` handling
- **State machine** — `transition_to()` enforces legal transitions; `CheckConstraint` guards valid status values
- **Retry** — two-phase beat task with `skip_locked=True`, exponential backoff, F()-based atomic counter
