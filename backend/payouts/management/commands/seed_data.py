"""
Seed script — creates 3 merchants with bank accounts and credit history.
Run: python manage.py seed_data
"""
import random
from django.core.management.base import BaseCommand
from django.db import transaction
from payouts.models import Merchant, BankAccount, LedgerEntry


MERCHANTS = [
    {"name": "Raj Digital Agency", "email": "raj@agency.in"},
    {"name": "Priya Freelance Design", "email": "priya@freelance.in"},
    {"name": "Amit SaaS Tools", "email": "amit@saastools.in"},
]

BANK_ACCOUNTS = [
    {"account_number": "1234567890", "ifsc": "SBIN0001234", "account_holder_name": "Raj Kumar"},
    {"account_number": "9876543210", "ifsc": "HDFC0004567", "account_holder_name": "Priya Sharma"},
    {"account_number": "1122334455", "ifsc": "ICIC0008901", "account_holder_name": "Amit Patel"},
]

CREDIT_AMOUNTS = [
    500_000,   # ₹5,000
    1_000_000, # ₹10,000
    750_000,   # ₹7,500
    300_000,   # ₹3,000
    250_000,   # ₹2,500
    2_000_000, # ₹20,000
]


class Command(BaseCommand):
    help = "Seed database with merchants, bank accounts, and credit history"

    def handle(self, *args, **options):
        self.stdout.write("Seeding database...")

        with transaction.atomic():
            for i, merchant_data in enumerate(MERCHANTS):
                merchant, created = Merchant.objects.get_or_create(
                    email=merchant_data["email"],
                    defaults={"name": merchant_data["name"]},
                )
                action = "Created" if created else "Existing"
                self.stdout.write(f"  {action} merchant: {merchant.name}")

                if created:
                    # Create bank account
                    BankAccount.objects.create(
                        merchant=merchant,
                        **BANK_ACCOUNTS[i],
                        is_active=True,
                    )

                    # Seed credits (simulated customer payments)
                    credits = random.sample(CREDIT_AMOUNTS, k=5)
                    for j, amount in enumerate(credits):
                        LedgerEntry.objects.create(
                            merchant=merchant,
                            entry_type=LedgerEntry.CREDIT,
                            status=LedgerEntry.FINAL,
                            amount_paise=amount,
                            description=f"Customer payment #{j+1}",
                        )
                    total = sum(credits)
                    self.stdout.write(
                        f"    → Credited {len(credits)} payments, total: ₹{total//100:,}"
                    )

        self.stdout.write(self.style.SUCCESS("✅ Seed complete!"))
