from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator


class ClientProfile(models.Model):

    account = models.OneToOneField(
        settings.ACCOUNT_MODEL,
        on_delete=models.CASCADE,
        related_name='client_profile',
    )

    # --- Company info (optional) ---
    company_name = models.CharField(max_length=150, blank=True)
    industry     = models.CharField(max_length=100, blank=True)
    website      = models.URLField(blank=True)

    # --- Reputation (cached, updated after each review) ---
    rating     = models.DecimalField(
        max_digits=3, decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    total_spent  = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_hires  = models.PositiveIntegerField(default=0)

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.account.user.username} — Client"