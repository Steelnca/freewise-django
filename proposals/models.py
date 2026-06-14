from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.mixins import PublicIDMixin

class Proposal(PublicIDMixin, models.Model):

    class Status(models.TextChoices):
        PENDING   = 'PENDING',   _('Pending')
        ACCEPTED  = 'ACCEPTED',  _('Accepted')
        REJECTED  = 'REJECTED',  _('Rejected')
        WITHDRAWN = 'WITHDRAWN', _('Withdrawn')

    PUBLIC_ID_PREFIX = "fwpp"

    # --- Relations ---
    job = models.ForeignKey(
        'jobs.Job',
        on_delete=models.CASCADE,
        related_name='proposals',
    )
    freelancer = models.ForeignKey(
        'freelancers.FreelancerProfile',
        on_delete=models.CASCADE,
        related_name='proposals',
    )

    # --- Proposal details ---
    cover_letter   = models.TextField()
    proposed_price = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_days  = models.PositiveIntegerField()

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        # One proposal per freelancer per job
        unique_together = ('job', 'freelancer')

    def __str__(self):
        return f"{self.freelancer} → {self.job} ({self.status})"