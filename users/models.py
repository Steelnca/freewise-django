

from allauth.account.models import EmailAddress

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.utils import timezone

from core.mixins import LowercaseFieldsMixin

from .validators import username_regex, username_profanity, username_reserved_terms
from .constants import USERNAME_MAX_LENGTH

# Create your models here.

class User(AbstractUser, LowercaseFieldsMixin):
    username = models.CharField(max_length=USERNAME_MAX_LENGTH, unique=True, validators=[username_regex, username_profanity, username_reserved_terms], db_index=True)
    is_staff = models.BooleanField(default=False)

    class ProcessOptions:
        lowercase_fields = ['username']

    def __str__(self) -> str:
        return self.username

    def get_user_email(user):
        email = EmailAddress.objects.filter(user=user, primary=True).first()
        return email.email if email else user.email  # Fallback to the email field on the User model



class UsernameChangeLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    old_username = models.CharField(max_length=USERNAME_MAX_LENGTH)
    changed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user.username} changed from {self.old_username} on {self.changed_at.date()}"