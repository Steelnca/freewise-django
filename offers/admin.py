from django.contrib import admin

from .models import Offer


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display    = ('freelancer', 'job', 'proposed_price', 'delivery_days', 'status', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('freelancer__account__user__username', 'job__title')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Relations', {
            'fields': ('job', 'freelancer'),
        }),
        ('Proposal', {
            'fields': ('cover_letter', 'proposed_price', 'delivery_days'),
        }),
        ('Status', {
            'fields': ('status',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )