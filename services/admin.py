from django.contrib import admin
from .models import Service, ServicePackage, Order


class ServicePackageInline(admin.TabularInline):
    model   = ServicePackage
    extra   = 0
    fields  = ('title', 'description', 'price', 'delivery_days', 'revisions', 'order')


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display    = ('title', 'freelancer', 'category', 'status', 'created_at')
    list_filter     = ('status', 'category')
    search_fields   = ('title', 'freelancer__account__user__username')
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('tags',)
    inlines         = [ServicePackageInline]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display    = ('id', 'service', 'client', 'package', 'status', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('service__title', 'client__account__user__username')
    readonly_fields = ('created_at', 'delivered_at', 'completed_at')
