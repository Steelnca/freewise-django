from phonenumber_field.modelfields import PhoneNumberField

from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils.text import slugify


class Account(models.Model):

    class Locale(models.TextChoices):
        EN = 'EN', _('English')
        AR = 'AR', _('Arabic')
        FR = 'FR', _('French')

    class Theme(models.TextChoices):
        LIGHT  = 'LIGHT',  _('Light')
        DARK   = 'DARK',   _('Dark')
        DEVICE = 'DEVICE', _('Device')

    user     = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='account')
    phone    = PhoneNumberField(null=True, blank=True)

    # Public profile
    avatar   = models.ImageField(upload_to='accounts/avatars/', null=True, blank=True)
    bio      = models.TextField(max_length=500, blank=True)
    slug     = models.SlugField(max_length=100, unique=True, blank=True)
    country  = models.CharField(max_length=255, blank=True)
    birthday = models.DateField(null=True, blank=True)

    # Preferences
    locale = models.CharField(max_length=10, choices=Locale.choices, default=Locale.EN)
    theme  = models.CharField(max_length=10, choices=Theme.choices, default=Theme.DEVICE)

    # Roles
    is_client     = models.BooleanField(default=False)
    is_freelancer = models.BooleanField(default=False)

    # Verification
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)

    # Timestamps
    joined_at  = models.DateField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s account"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.user.username)
        super().save(*args, **kwargs)

    def activate_client(self):
        from clients.models import ClientProfile
        self.is_client = True
        self.save(update_fields=['is_client'])
        ClientProfile.objects.get_or_create(account=self)

    def activate_freelancer(self):
        from freelancers.models import FreelancerProfile
        self.is_freelancer = True
        self.save(update_fields=['is_freelancer'])
        FreelancerProfile.objects.get_or_create(account=self)

    def deactivate_client(self):
        self.is_client = False
        self.save(update_fields=['is_client'])

    def deactivate_freelancer(self):
        self.is_freelancer = False
        self.save(update_fields=['is_freelancer'])