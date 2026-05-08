from django.contrib import admin

from .models import CollabPost, CollabApplication, CollabMember


class CollabApplicationInline(admin.TabularInline):
    model           = CollabApplication
    extra           = 0
    readonly_fields = ('created_at',)
    fields          = ('applicant', 'message', 'status', 'created_at')


class CollabMemberInline(admin.TabularInline):
    model           = CollabMember
    extra           = 0
    readonly_fields = ('joined_at',)
    fields          = ('freelancer', 'role_description', 'joined_at')


@admin.register(CollabPost)
class CollabPostAdmin(admin.ModelAdmin):
    list_display    = ('title', 'posted_by', 'spots', 'status', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('title', 'posted_by__account__user__username')
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('skills_needed',)
    inlines         = [CollabApplicationInline, CollabMemberInline]
    fieldsets = (
        ('Author', {
            'fields': ('posted_by',),
        }),
        ('Post Details', {
            'fields': ('title', 'description', 'skills_needed', 'spots'),
        }),
        ('Status', {
            'fields': ('status',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(CollabApplication)
class CollabApplicationAdmin(admin.ModelAdmin):
    list_display    = ('applicant', 'collab_post', 'status', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('applicant__account__user__username', 'collab_post__title')
    readonly_fields = ('created_at',)


@admin.register(CollabMember)
class CollabMemberAdmin(admin.ModelAdmin):
    list_display  = ('freelancer', 'collab_post', 'role_description', 'joined_at')
    search_fields = ('freelancer__account__user__username', 'collab_post__title')
    readonly_fields = ('joined_at',)