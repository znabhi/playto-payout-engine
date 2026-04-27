from django.contrib import admin
from .models import Merchant, BankAccount, LedgerEntry, IdempotencyKey, Payout


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "created_at"]
    search_fields = ["name", "email"]


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ["account_holder_name", "account_number", "ifsc", "merchant", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["account_holder_name", "account_number", "merchant__name"]


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ["merchant", "entry_type", "status", "amount_paise", "description", "created_at"]
    list_filter = ["entry_type", "status"]
    search_fields = ["merchant__name", "description"]
    ordering = ["-created_at"]


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ["merchant", "key", "status", "expires_at", "created_at"]
    list_filter = ["status"]
    search_fields = ["merchant__name", "key"]


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ["merchant", "amount_paise", "status", "attempt_count", "created_at", "updated_at"]
    list_filter = ["status"]
    search_fields = ["merchant__name"]
    ordering = ["-created_at"]
