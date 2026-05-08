from django.contrib import admin

from .models import EscrowTransaction, Payout, ChargilyWebhookLog


@admin.register(EscrowTransaction)
class EscrowTransactionAdmin(admin.ModelAdmin):
    list_display    = ('id', 'milestone', 'amount', 'platform_fee', 'freelancer_gets', 'status', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('chargily_checkout_id', 'chargily_payment_id', 'milestone__contract__client__account__user__username')
    readonly_fields = ('created_at', 'paid_at', 'released_at')
    fieldsets = (
        ('Milestone', {
            'fields': ('milestone',),
        }),
        ('Amounts', {
            'fields': ('amount', 'platform_fee', 'freelancer_gets'),
        }),
        ('Chargily', {
            'fields': ('chargily_checkout_id', 'chargily_payment_id'),
        }),
        ('Status', {
            'fields': ('status',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'paid_at', 'released_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display    = ('id', 'freelancer', 'amount', 'status', 'reference', 'created_at', 'paid_at')
    list_filter     = ('status',)
    search_fields   = ('freelancer__account__user__username', 'reference')
    readonly_fields = ('created_at', 'paid_at')
    fieldsets = (
        ('Relations', {
            'fields': ('escrow', 'freelancer'),
        }),
        ('Payout Details', {
            'fields': ('amount', 'status', 'reference', 'notes'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'paid_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(ChargilyWebhookLog)
class ChargilyWebhookLogAdmin(admin.ModelAdmin):
    list_display    = ('event_id', 'event_type', 'processed', 'received_at')
    list_filter     = ('event_type', 'processed')
    search_fields   = ('event_id', 'event_type')
    readonly_fields = ('event_id', 'event_type', 'payload', 'received_at')

    def has_add_permission(self, request):
        return False  # webhook logs are system-generated only

    def has_change_permission(self, request, obj=None):
        return False  # read-only in admin