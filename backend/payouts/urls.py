from django.urls import path
from . import views

urlpatterns = [
    # Merchants
    path("merchants/", views.merchant_list, name="merchant-list"),
    path("merchants/<uuid:merchant_id>/balance/", views.merchant_balance, name="merchant-balance"),
    path("merchants/<uuid:merchant_id>/ledger/", views.merchant_ledger, name="merchant-ledger"),
    path("merchants/<uuid:merchant_id>/bank-accounts/", views.merchant_bank_accounts, name="merchant-bank-accounts"),
    # Payouts
    path("payouts/", views.create_payout, name="payout-create-or-list"),
    path("payouts/list/", views.payout_list, name="payout-list"),
    path("payouts/<uuid:payout_id>/", views.payout_detail, name="payout-detail"),
]
