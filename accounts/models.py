

from phonenumber_field.modelfields import PhoneNumberField

from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.urls import reverse

# Create your models here.

class Account(models.Model):

    class Locale(models.TextChoices):
        EN = 'EN', _('English')
        AR = 'AR', _('Arabic')
        FR = 'FR', _('French')

    class Theme(models.TextChoices):
        LIGHT = 'LIGHT', _('Light')
        DARK = 'DARK', _('Dark')
        DEVICE = 'DEVICE', ('Device')

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='account')

    phone = PhoneNumberField(null=True, blank=True)

    locale = models.CharField(max_length=255, choices=Locale.choices, default=Locale.EN)
    theme = models.CharField(max_length=255, choices=Theme.choices, default=Theme.DEVICE)

    joined_at = models.DateField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.username}\'s account'