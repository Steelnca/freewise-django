from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import SubscriptionPlan, SubscriptionPlanPrice, FreelancerSubscription, ClientSubscription


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "role",
        "slug",
        "max_open_bids",
        "max_active_contracts",
        "max_jobs_posted",
        "max_active_jobs",
        "is_default",
        "is_active",
        "created_at",
    )
    list_filter = (
        "role",
        "is_default",
        "is_active",
    )
    search_fields = (
        "name",
        "slug",
        "description",
    )
    ordering = ("role", "created_at")
    readonly_fields = ("public_id", "created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}

    fieldsets = (
        (_("Plan"), {"fields": ("public_id", "role", "name", "slug", "description")}),
        (_("Status"), {"fields": ("is_default", "is_active")}),
        (_("Freelancer limits"), {"fields": ("max_open_bids", "max_active_contracts")}),
        (_("Client limits"), {"fields": ("max_jobs_posted", "max_active_jobs")}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at")}),
    )

    actions = ("make_default", "disable_plans", "enable_plans")

    @admin.action(description="Set selected plans as default for their role")
    def make_default(self, request, queryset):
        roles = set(queryset.values_list("role", flat=True))
        if len(roles) != 1:
            self.message_user(request, "Pick plans from only one role at a time.", level="warning")
            return

        role = next(iter(roles))
        plan = queryset.first()
        SubscriptionPlan.objects.filter(role=role).update(is_default=False)
        plan.is_default = True
        plan.save(update_fields=["is_default", "updated_at"])
        self.message_user(request, f"{plan.name} is now the default {role.lower()} plan.")

    @admin.action(description="Disable selected plans")
    def disable_plans(self, request, queryset):
        queryset.update(is_active=False)

    @admin.action(description="Enable selected plans")
    def enable_plans(self, request, queryset):
        queryset.update(is_active=True)


@admin.register(SubscriptionPlanPrice)
class SubscriptionPlanPriceAdmin(admin.ModelAdmin):
    list_display = (
        "plan",
        "billing_cycle",
        "price",
        "is_active",
    )

    list_filter = (
        "billing_cycle",
        "is_active",
        "plan__role",
    )

    search_fields = (
        "plan__name",
        "plan__slug",
    )

    autocomplete_fields = ("plan",)

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )


@admin.register(FreelancerSubscription)
class FreelancerSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "freelancer",
        "plan",
        "status",
        "auto_renew",
        "starts_at",
        "ends_at",
        "created_at",
    )
    list_filter = ("status", "auto_renew", "plan")
    search_fields = (
        "freelancer__username",
        "freelancer__slug",
        "plan__name",
        "provider_name",
        "provider_reference",
    )
    ordering = ("-created_at",)
    readonly_fields = ("public_id", "starts_at", "created_at", "updated_at")
    raw_id_fields = ("freelancer", "plan")

    fieldsets = (
        (_("Subscription"), {"fields": ("public_id", "freelancer", "plan", "status")}),
        (_("Billing"), {"fields": ("starts_at", "ends_at", "auto_renew", "provider_name", "provider_reference")}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at")}),
    )


@admin.register(ClientSubscription)
class ClientSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "plan",
        "status",
        "auto_renew",
        "starts_at",
        "ends_at",
        "created_at",
    )
    list_filter = ("status", "auto_renew", "plan")
    search_fields = (
        "client__username",
        "client__slug",
        "plan__name",
        "provider_name",
        "provider_reference",
    )
    ordering = ("-created_at",)
    readonly_fields = ("public_id", "starts_at", "created_at", "updated_at")
    raw_id_fields = ("client", "plan")

    fieldsets = (
        (_("Subscription"), {"fields": ("public_id", "client", "plan", "status")}),
        (_("Billing"), {"fields": ("starts_at", "ends_at", "auto_renew", "provider_name", "provider_reference")}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at")}),
    )