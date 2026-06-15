
from django.contrib import admin
from .models import User

# Register your models here.

class UserAdmin(admin.ModelAdmin):
    model = User
    search_fields = ['id', 'username']

admin.site.register(User, UserAdmin)