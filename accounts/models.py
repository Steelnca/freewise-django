

from phonenumber_field.modelfields import PhoneNumberField

from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.urls import reverse

# Create your models here.

class Avatar(models.Model):
    image = models.ImageField(upload_to='profiles/avatars/')
    title = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    is_default = models.BooleanField(default=False, verbose_name=_('Default'))

    def __str__(self):
        return self.title

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

    avatar = models.ForeignKey(Avatar, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('Avatar'))
    birthday = models.DateField(null=True, blank=True, verbose_name=_('Birthday'))
    country = models.CharField(max_length=255, blank=True, verbose_name=_('Country'))

    joined_at = models.DateField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_client = models.BooleanField(default=False)
    is_freelancer = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.user.username}\'s account'

    def activate_client(self):
        self.is_client = True
        self.is_freelancer = False
        self.save()

    def activate_freelancer(self):
        self.is_freelancer = True
        self.is_client = False
        self.save()
