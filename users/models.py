

from allauth.account.models import EmailAddress

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError

from core.mixins import LowercaseFieldsMixin

from .validators import username_regex, username_profanity, username_reserved_terms
from .constants import USERNAME_MAX_LENGTH

# Create your models here.

class User(AbstractUser, LowercaseFieldsMixin):

    class Type(models.TextChoices):
        REGULAR = 'regular', _('Regular User')
        PLATFORM = 'platform', _('Platform Account')

    username = models.CharField(max_length=USERNAME_MAX_LENGTH, unique=True, validators=[username_regex, username_profanity, username_reserved_terms], db_index=True)
    is_staff = models.BooleanField(default=False)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.REGULAR)

    token_version = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Used to invalidate existing JWT sessions. "
            "Incrementing this value revokes all previously issued access "
            "and refresh tokens for the user."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["type"],
                condition=models.Q(type='platform'),
                name="unique_platform_user",
            ),
        ]

    class ProcessOptions:
        lowercase_fields = ['username']

    def __str__(self) -> str:
        return self.username

    def get_user_email(user):
        email = EmailAddress.objects.filter(user=user, primary=True).first()
        return email.email if email else user.email  # Fallback to the email field on the User model

    @classmethod
    def get_platform_user(cls):
        try:
            platform_user, created = cls.objects.get_or_create(
                username=settings.FREEWISE_PLATFORM_USERNAME,
                type=cls.Type.PLATFORM,
                defaults={
                    "email": settings.FREEWISE_PLATFORM_EMAIL,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )

            # Keep it unusable as a login account
            if created:
                platform_user.set_unusable_password()
                platform_user.save(update_fields=["password"])
            else:
                # Repair it if someone accidentally gave it a usable password
                if platform_user.has_usable_password():
                    platform_user.set_unusable_password()
                    platform_user.save(update_fields=["password"])

            return platform_user

        except cls.MultipleObjectsReturned:
            raise ValidationError("Multiple platform users found.")



class UsernameChangeLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    old_username = models.CharField(max_length=USERNAME_MAX_LENGTH)
    changed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user.username} changed from {self.old_username} on {self.changed_at.date()}"


class PhoneOTP(models.Model):
    """6-digit OTP for phone number verification."""
    account    = models.ForeignKey('accounts.Account', on_delete=models.CASCADE, related_name='phone_otps')
    code       = models.CharField(max_length=6)
    used       = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    OTP_EXPIRY_MINUTES = 10

    class Meta:
        ordering = ['-created_at']

    def is_expired(self):
        return timezone.now() > self.created_at + timezone.timedelta(minutes=self.OTP_EXPIRY_MINUTES)

    def __str__(self):
        return f"OTP for {self.account} ({'used' if self.used else 'active'})"