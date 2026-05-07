from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator


class Review(models.Model):
    """
    Both sides review each other after a contract is completed.
    reviewer → reviewee (client reviews freelancer, freelancer reviews client).
    """

    contract = models.ForeignKey(
        'contracts.Contract',
        on_delete=models.CASCADE,
        related_name='reviews',
    )
    reviewer = models.ForeignKey(
        'accounts.Account',
        on_delete=models.CASCADE,
        related_name='reviews_given',
    )
    reviewee = models.ForeignKey(
        'accounts.Account',
        on_delete=models.CASCADE,
        related_name='reviews_received',
    )

    # --- Rating ---
    rating  = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    comment = models.TextField(blank=True)

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        # one review per reviewer per contract
        unique_together = ('contract', 'reviewer')

    def __str__(self):
        return f"{self.reviewer} → {self.reviewee} ({self.rating}★)"