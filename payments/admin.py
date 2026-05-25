# payments/admin.py
"""
Payments admin for Freewise.

Money records are read-only.
Moderator actions are limited to operational safety controls.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Wallet, WalletTransaction, EscrowHold, Payout, WebhookLog


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "currency", "available_balance", "escrow_balance", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at", "available_balance", "escrow_balance")
    actions = ["freeze_wallet", "unfreeze_wallet"]

    def freeze_wallet(self, request, queryset):
        queryset.update(status=Wallet.Status.FROZEN)
    freeze_wallet.short_description = _("Freeze selected wallets")

    def unfreeze_wallet(self, request, queryset):
        queryset.update(status=Wallet.Status.ACTIVE)
    unfreeze_wallet.short_description = _("Unfreeze selected wallets")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "transaction_type", "status", "amount", "currency", "created_at")
    list_filter = ("transaction_type", "status", "currency")
    search_fields = ("wallet__user__username", "wallet__user__email", "idempotency_key", "reference_id")
    readonly_fields = [f.name for f in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EscrowHold)
class EscrowHoldAdmin(admin.ModelAdmin):
    list_display = ("id", "contract_reference", "wallet", "amount", "currency", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("contract_reference", "wallet__user__username", "wallet__user__email")
    readonly_fields = [f.name for f in EscrowHold._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "amount", "currency", "status", "provider_name", "created_at")
    list_filter = ("status", "provider_name", "currency")
    search_fields = ("wallet__user__username", "wallet__user__email", "provider_reference", "destination_label")
    readonly_fields = [f.name for f in Payout._meta.fields]
    actions = ["mark_processing", "mark_failed"]

    def mark_processing(self, request, queryset):
        queryset.update(status=Payout.Status.PROCESSING)
    mark_processing.short_description = _("Mark selected payouts as processing")

    def mark_failed(self, request, queryset):
        queryset.update(status=Payout.Status.FAILED)
    mark_failed.short_description = _("Mark selected payouts as failed")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    list_display = ("provider_name", "event_name", "status", "signature_valid", "processed", "created_at")
    list_filter = ("provider_name", "status", "signature_valid", "processed")
    search_fields = ("provider_event_id", "event_name", "processing_error")
    readonly_fields = [f.name for f in WebhookLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False