from django.contrib import admin

from .models import Category, Tag, Job


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display        = ('name', 'slug', 'icon')
    search_fields       = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display        = ('name', 'slug')
    search_fields       = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display    = ('title', 'client', 'category', 'status', 'budget_total', 'deadline', 'created_at')
    list_filter     = ('status', 'experience_level', 'category')
    search_fields   = ('title', 'description', 'client__account__user__username')
    readonly_fields = ('public_id', 'created_at', 'updated_at')
    filter_horizontal = ('tags',)
    fieldsets = (
        ('Identifiers', {
            'fields': ('public_id',),
        }),
        ('Ownership', {
            'fields': ('client',),
        }),
        ('Job Details', {
            'fields': ('title', 'description', 'category', 'tags', 'experience_level'),
        }),
        ('Budget & Deadline', {
            'fields': ('budget_total', 'deadline'),
        }),
        ('Status', {
            'fields': ('status',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )