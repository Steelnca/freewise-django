from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from contracts.services import resolve_dispute_to_freelancer, resolve_dispute_to_client
from .models import Milestone, MilestonePlan, MilestonePlanItem, MilestoneSubmission

# Register your models here.

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "public_id",
        "title",
        "contract",
        "amount",
        "currency",
        "status",
        "due_date",
        "order",
        "created_at",
    )
    list_filter = ("status", "currency", "created_at")
    search_fields = (
        "title",
        "contract__title",
        "contract__client__account__user__username",
        "contract__freelancer__account__user__username",
    )
    ordering = ("contract", "order", "-created_at")
    readonly_fields = (
        "id",
        "public_id",
        "created_at",
        "updated_at",
        "funded_at",
        "submitted_at",
        "approved_at",
        "released_at",
        "refunded_at",
        "disputed_at",
        "review_due_at",
    )
    fieldsets = (
        (_("Identifiers"), {"fields": ("public_id",)}),
        (_("Details"), {"fields": ("contract", "title", "description", "currency", "amount", "due_date", "order")}),
        (_("Status"), {"fields": ("status", "submission_note", "review_note", "dispute_reason")}),
        (
            _("Timestamps"),
            {
                "fields": (
                    "funded_at",
                    "submitted_at",
                    "approved_at",
                    "released_at",
                    "refunded_at",
                    "disputed_at",
                    "review_due_at",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.action(description="Resolve dispute to freelancer")
    def resolve_to_freelancer(self, request, queryset):
        for milestone in queryset:
            resolve_dispute_to_freelancer(milestone=milestone, user=request.user)

    @admin.action(description="Resolve dispute to client")
    def resolve_to_client(self, request, queryset):
        for milestone in queryset:
            resolve_dispute_to_client(milestone=milestone, user=request.user)

class MilestonePlanItemInline(admin.TabularInline):
    model = MilestonePlanItem
    extra = 0

    fields = (
        "order",
        "title",
        "amount",
        "due_date",
        "status",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = ("order",)


@admin.register(MilestonePlan)
class MilestonePlanAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "job",
        "proposal",
        "source_role",
        "status",
        "is_selected",
        "total_amount",
        "currency",
        "created_by",
        "created_at",
    )

    list_filter = (
        "status",
        "source_role",
        "is_selected",
        "currency",
        "created_at",
    )

    search_fields = (
        "public_id",
        "job__title",
        "proposal__public_id",
        "created_by__username",
    )

    readonly_fields = (
        "public_id",
        "total_amount",
        "selected_at",
        "created_at",
        "updated_at",
    )

    ordering = ("-created_at",)

    autocomplete_fields = (
        "job",
        "proposal",
        "created_by",
    )

    inlines = [MilestonePlanItemInline]

    fieldsets = (
        (
            "Relations",
            {
                "fields": (
                    "job",
                    "proposal",
                    "created_by",
                )
            },
        ),
        (
            "Plan",
            {
                "fields": (
                    "source_role",
                    "status",
                    "is_selected",
                    "selected_at",
                    "note",
                    "suggestion_enabled",
                )
            },
        ),
        (
            "Financial",
            {
                "fields": (
                    "currency",
                    "total_amount",
                )
            },
        ),
        (
            "System",
            {
                "classes": ("collapse",),
                "fields": (
                    "public_id",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )


@admin.register(MilestonePlanItem)
class MilestonePlanItemAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "plan",
        "order",
        "amount",
        "status",
        "due_date",
        "created_at",
    )

    list_filter = (
        "status",
        "created_at",
        "due_date",
    )

    search_fields = (
        "title",
        "description",
        "plan__public_id",
        "plan__job__title",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    autocomplete_fields = (
        "plan",
    )

    ordering = (
        "plan",
        "order",
    )

    fieldsets = (
        (
            "Plan Item",
            {
                "fields": (
                    "plan",
                    "title",
                    "description",
                    "order",
                )
            },
        ),
        (
            "Delivery",
            {
                "fields": (
                    "amount",
                    "due_date",
                    "status",
                )
            },
        ),
        (
            "System",
            {
                "classes": ("collapse",),
                "fields": (
                    "public_id",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )