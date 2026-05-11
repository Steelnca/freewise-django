from django.db import models
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):

    class Type(models.TextChoices):
        # Job flow
        JOB_NEW_PROPOSAL      = 'JOB_NEW_PROPOSAL',       _('New proposal on your job')
        PROPOSAL_ACCEPTED      = 'PROPOSAL_ACCEPTED',       _('Your proposal was accepted')
        PROPOSAL_REJECTED      = 'PROPOSAL_REJECTED',       _('Your proposal was rejected')
        # Contract & payment
        CONTRACT_STARTED    = 'CONTRACT_STARTED',     _('Contract started')
        PAYMENT_HELD        = 'PAYMENT_HELD',         _('Payment is in escrow')
        MILESTONE_SUBMITTED = 'MILESTONE_SUBMITTED',  _('Milestone submitted for review')
        MILESTONE_APPROVED  = 'MILESTONE_APPROVED',   _('Milestone approved')
        PAYOUT_SENT         = 'PAYOUT_SENT',          _('Payout has been sent')
        # Disputes
        DISPUTE_OPENED      = 'DISPUTE_OPENED',       _('A dispute was opened')
        DISPUTE_RESOLVED    = 'DISPUTE_RESOLVED',     _('Dispute resolved')
        # Collabs
        COLLAB_APPLICATION  = 'COLLAB_APPLICATION',   _('New collab application')
        COLLAB_ACCEPTED     = 'COLLAB_ACCEPTED',      _('Collab application accepted')
        # Reviews
        REVIEW_RECEIVED     = 'REVIEW_RECEIVED',      _('You received a review')
        # General
        GENERAL             = 'GENERAL',              _('General')

    account = models.ForeignKey(
        'accounts.Account',
        on_delete=models.CASCADE,
        related_name='notifications',
    )

    type    = models.CharField(max_length=30, choices=Type.choices, default=Type.GENERAL, db_index=True)
    title   = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)

    # Optional deep link to the relevant object
    link = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.type}] → {self.account} ({'read' if self.is_read else 'unread'})"