from django.db import models
from django.utils.translation import gettext_lazy as _


class EscrowTransaction(models.Model):

    class Status(models.TextChoices):
        PENDING  = 'PENDING',  _('Pending')   # checkout created, awaiting payment
        HELD     = 'HELD',     _('Held')      # payment confirmed, funds in escrow
        RELEASED = 'RELEASED', _('Released')  # payout sent to freelancer
        REFUNDED = 'REFUNDED', _('Refunded')  # funds returned to client
        DISPUTED = 'DISPUTED', _('Disputed')  # dispute opened, funds frozen

    milestone = models.OneToOneField(
        'contracts.Milestone',
        on_delete=models.PROTECT,
        related_name='escrow',
    )

    # --- Amounts ---
    amount          = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee    = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    freelancer_gets = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # --- Chargily references ---
    chargily_checkout_id = models.CharField(max_length=255, blank=True, db_index=True)
    chargily_payment_id  = models.CharField(max_length=255, blank=True, db_index=True)

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # --- Timestamps ---
    created_at  = models.DateTimeField(auto_now_add=True)
    paid_at     = models.DateTimeField(null=True, blank=True)  # when Chargily confirms
    released_at = models.DateTimeField(null=True, blank=True)  # when payout is triggered

    def __str__(self):
        return f"Escrow #{self.pk} — {self.amount} DZD ({self.status})"


class Payout(models.Model):

    class Status(models.TextChoices):
        PENDING    = 'PENDING',    _('Pending')
        PROCESSING = 'PROCESSING', _('Processing')
        PAID       = 'PAID',       _('Paid')
        FAILED     = 'FAILED',     _('Failed')

    escrow = models.OneToOneField(
        EscrowTransaction,
        on_delete=models.PROTECT,
        related_name='payout',
    )
    freelancer = models.ForeignKey(
        'freelancers.FreelancerProfile',
        on_delete=models.PROTECT,
        related_name='payouts',
    )

    # --- Amount ---
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # --- Reference (bank transfer, CCP, or future Chargily payout ID) ---
    reference = models.CharField(max_length=255, blank=True)
    notes     = models.TextField(blank=True)

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at    = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Payout #{self.pk} — {self.amount} DZD → {self.freelancer} ({self.status})"


class ChargilyWebhookLog(models.Model):
    """Audit log for every webhook event received from Chargily."""

    event_id   = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    payload    = models.JSONField()
    processed  = models.BooleanField(default=False)
    error      = models.TextField(blank=True)  # store any processing error

    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f"Webhook {self.event_type} — {self.event_id} ({'ok' if self.processed else 'unprocessed'})"