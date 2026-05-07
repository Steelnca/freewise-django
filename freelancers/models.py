
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator

class FreelancerProfile(models.Model):

    class Availability(models.TextChoices):
        AVAILABLE   = 'AVAILABLE',   _('Available')
        BUSY        = 'BUSY',        _('Busy')
        UNAVAILABLE = 'UNAVAILABLE', _('Unavailable')

    account = models.OneToOneField(
        settings.ACCOUNT_MODEL,
        on_delete=models.CASCADE,
        related_name='freelancer_profile',
    )

    # --- Professional info ---
    title         = models.CharField(max_length=120, blank=True)
    bio           = models.TextField(max_length=1000, blank=True)
    hourly_rate   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    portfolio_url = models.URLField(blank=True)
    availability  = models.CharField(
        max_length=20,
        choices=Availability.choices,
        default=Availability.AVAILABLE,
    )

    # --- Reputation (cached, updated after each review) ---
    rating      = models.DecimalField(
        max_digits=3, decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    total_earned    = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    completed_jobs  = models.PositiveIntegerField(default=0)

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.account.user.username} — Freelancer"


class Skill(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)

    def __str__(self):
        return self.name


class FreelancerSkill(models.Model):
    freelancer = models.ForeignKey(
        FreelancerProfile,
        on_delete=models.CASCADE,
        related_name='skills',
    )
    skill = models.ForeignKey(
        Skill,
        on_delete=models.CASCADE,
        related_name='freelancers',
    )

    class Meta:
        unique_together = ('freelancer', 'skill')

    def __str__(self):
        return f"{self.freelancer} — {self.skill}"