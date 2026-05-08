from django.contrib import admin

from .models import Contract, Milestone


class MilestoneInline(admin.TabularInline):
    model   = Milestone
    extra   = 0
    readonly_fields = ('created_at', 'submitted_at', 'approved_at')
    fields  = ('title', 'amount', 'due_date', 'order', 'status', 'submitted_at', 'approved_at')


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display    = ('id', 'client', 'freelancer', 'agreed_price', 'status', 'deadline', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('client__account__user__username', 'freelancer__account__user__username')
    readonly_fields = ('created_at', 'updated_at', 'completed_at')
    inlines         = [MilestoneInline]
    fieldsets = (
        ('Parties', {
            'fields': ('client', 'freelancer'),
        }),
        ('Source', {
            'fields': ('job', 'offer'),
        }),
        ('Terms', {
            'fields': ('agreed_price', 'deadline'),
        }),
        ('Status', {
            'fields': ('status',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'completed_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display    = ('title', 'contract', 'amount', 'status', 'due_date', 'order')
    list_filter     = ('status',)
    search_fields   = ('title', 'contract__client__account__user__username')
    readonly_fields = ('created_at', 'submitted_at', 'approved_at')