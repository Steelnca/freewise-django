
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


class Profile(models.Model):

    account = models.ForeignKey(settings.ACCOUNT_MODEL, on_delete=models.CASCADE, related_name='profile')

    avatar = models.ForeignKey(Avatar, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('Avatar'))
    birthday = models.DateField(null=True, blank=True, verbose_name=_('Birthday'))
    country = models.CharField(max_length=255, blank=True, verbose_name=_('Country'))

    slug = models.SlugField(max_length=255, blank=True, unique=True)

    def get_absolute_url(self):
        return reverse('accounts:profile')

    def __str__(self):
        return self.slug