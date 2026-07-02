"""
Freewise contract admin.

This is for visibility and moderation, not casual editing.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from milestones.models import Milestone

from .models import Contract


class MilestoneInline(admin.TabularInline):
    model = Milestone
    extra = 0
    can_delete = False
    fields = (
        "title",
        "amount",
        "currency",
        "due_date",
        "order",
        "status",
        "funded_at",
        "submitted_at",
        "approved_at",
        "released_at",
        "refunded_at",
    )
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "public_id",
        "display_name",
        "source_type",
        "client",
        "freelancer",
        "agreed_price",
        "currency",
        "status",
        "deadline",
        "created_at",
    )
    list_filter = ("status", "source_type", "currency", "created_at")
    search_fields = (
        "title",
        "client__account__user__username",
        "client__account__user__email",
        "freelancer__account__user__username",
        "freelancer__account__user__email",
    )
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "public_id",
        "created_at",
        "updated_at",
        "active_at",
        "completed_at",
        "suspended_at",
        "withdrawn_at",
        "cancelled_at",

    )
    inlines = [MilestoneInline]

    fieldsets = (
        (_("Identifiers"), {"fields": ("public_id",)}),
        (_("Source"), {"fields": ("source_type", "job", "proposal", "title")}),
        (_("Parties"), {"fields": ("client", "freelancer")}),
        (_("Terms"), {"fields": ("currency", "agreed_price", "deadline")}),
        (_("Status"), {"fields": ("status", "notes")}),
        (
            _("Timestamps"),
            {
                "fields": (
                    "active_at",
                    "completed_at",
                    "cancelled_at",
                    "suspended_at",
                    "withdrawn_at",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self.readonly_fields
        return self.readonly_fields

