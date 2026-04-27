from rest_framework import serializers
from .models import Merchant, BankAccount, LedgerEntry, Payout


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = ["id", "name", "email", "created_at"]


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ["id", "account_number", "ifsc", "account_holder_name", "is_active"]


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = [
            "id",
            "entry_type",
            "status",
            "amount_paise",
            "description",
            "reference",
            "created_at",
        ]


class PayoutSerializer(serializers.ModelSerializer):
    bank_account = BankAccountSerializer(read_only=True)

    class Meta:
        model = Payout
        fields = [
            "id",
            "merchant",
            "bank_account",
            "amount_paise",
            "status",
            "attempt_count",
            "last_attempted_at",
            "created_at",
            "updated_at",
        ]
