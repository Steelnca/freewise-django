from django.contrib import admin

from .models import FreelancerProfile, Skill, FreelancerSkill


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display  = ('name', 'slug')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


class FreelancerSkillInline(admin.TabularInline):
    model = FreelancerSkill
    extra = 1
    autocomplete_fields = ('skill',)


@admin.register(FreelancerProfile)
class FreelancerProfileAdmin(admin.ModelAdmin):
    list_display    = ('account', 'title', 'hourly_rate', 'availability', 'rating', 'completed_jobs', 'total_earned')
    list_filter     = ('availability',)
    search_fields   = ('account__user__username', 'account__user__email', 'title')
    readonly_fields = ('rating', 'total_earned', 'completed_jobs', 'created_at', 'updated_at')
    inlines         = [FreelancerSkillInline]
    fieldsets = (
        ('Account', {
            'fields': ('account',),
        }),
        ('Professional Info', {
            'fields': ('title', 'bio', 'hourly_rate', 'portfolio_url', 'availability'),
        }),
        ('Reputation', {
            'fields': ('rating', 'completed_jobs', 'total_earned'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )