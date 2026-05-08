from django.contrib import admin

from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display    = ('reviewer', 'reviewee', 'contract', 'rating', 'created_at')
    list_filter     = ('rating',)
    search_fields   = ('reviewer__user__username', 'reviewee__user__username')
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Parties', {
            'fields': ('contract', 'reviewer', 'reviewee'),
        }),
        ('Review', {
            'fields': ('rating', 'comment'),
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )