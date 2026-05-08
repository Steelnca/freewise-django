from django.contrib import admin

from .models import ClientProfile


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display    = ('account', 'company_name', 'industry', 'rating', 'total_spent', 'total_hires')
    list_filter     = ('industry',)
    search_fields   = ('account__user__username', 'account__user__email', 'company_name')
    readonly_fields = ('rating', 'total_spent', 'total_hires', 'created_at', 'updated_at')
    fieldsets = (
        ('Account', {
            'fields': ('account',),
        }),
        ('Company Info', {
            'fields': ('company_name', 'industry', 'website'),
        }),
        ('Reputation', {
            'fields': ('rating', 'total_spent', 'total_hires'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )