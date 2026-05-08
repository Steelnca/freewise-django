from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display    = ('account', 'type', 'title', 'is_read', 'created_at')
    list_filter     = ('type', 'is_read')
    search_fields   = ('account__user__username', 'title', 'message')
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Recipient', {
            'fields': ('account',),
        }),
        ('Content', {
            'fields': ('type', 'title', 'message', 'link'),
        }),
        ('Status', {
            'fields': ('is_read',),
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )

    actions = ['mark_as_read', 'mark_as_unread']

    @admin.action(description='Mark selected notifications as read')
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)

    @admin.action(description='Mark selected notifications as unread')
    def mark_as_unread(self, request, queryset):
        queryset.update(is_read=False)