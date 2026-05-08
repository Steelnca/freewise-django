
from django.contrib import admin

from .models import Account

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display    = ('user', 'is_client', 'is_freelancer', 'country', 'locale', 'joined_at')
    list_filter     = ('is_client', 'is_freelancer', 'locale', 'theme', 'country')
    search_fields   = ('user__username', 'user__email', 'slug', 'phone')
    readonly_fields = ('slug', 'joined_at', 'updated_at')
    fieldsets = (
        ('User', {
            'fields': ('user',),
        }),
        ('Public Profile', {
            'fields': ('avatar', 'bio', 'slug', 'country', 'birthday'),
        }),
        ('Contact', {
            'fields': ('phone',),
        }),
        ('Roles', {
            'fields': ('is_client', 'is_freelancer'),
        }),
        ('Preferences', {
            'fields': ('locale', 'theme'),
        }),
        ('Timestamps', {
            'fields': ('joined_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )