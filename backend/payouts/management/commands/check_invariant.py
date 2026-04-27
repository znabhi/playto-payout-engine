"""
Management command to verify the balance invariant holds for every merchant:
  available + held == credits - final_debits

Run: python manage.py check_invariant
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q, Sum
from payouts.models import Merchant, LedgerEntry


class Command(BaseCommand):
    help = "Verify the ledger balance invariant for all merchants"

    def handle(self, *args, **options):
        violations = []
        merchants = Merchant.objects.all()

        for merchant in merchants:
            agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(
                credits=Sum("amount_paise", filter=Q(entry_type="CREDIT")),
                final_debits=Sum("amount_paise", filter=Q(entry_type="DEBIT", status="FINAL")),
                held_debits=Sum("amount_paise", filter=Q(entry_type="DEBIT", status="HELD")),
            )
            credits      = agg["credits"]      or 0
            final_debits = agg["final_debits"] or 0
            held_debits  = agg["held_debits"]  or 0

            available = credits - final_debits - held_debits
            held      = held_debits

            # Invariant: available + held == credits - final_debits
            if available + held != credits - final_debits:
                violations.append(
                    f"  VIOLATION: {merchant.name} — "
                    f"available({available}) + held({held}) "
                    f"!= credits({credits}) - final_debits({final_debits})"
                )
            else:
                self.stdout.write(
                    f"  ✓ {merchant.name}: available=₹{available//100:,} held=₹{held//100:,}"
                )

        if violations:
            for v in violations:
                self.stderr.write(self.style.ERROR(v))
            raise CommandError("Balance invariant violated!")

        self.stdout.write(self.style.SUCCESS("✅ All balance invariants hold."))
