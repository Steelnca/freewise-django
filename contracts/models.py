from django.db import models
from django.utils.translation import gettext_lazy as _


class Contract(models.Model):

    class Status(models.TextChoices):
        ACTIVE    = 'ACTIVE',    _('Active')
        COMPLETED = 'COMPLETED', _('Completed')
        DISPUTED  = 'DISPUTED',  _('Disputed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    # --- Relations ---
    # Null when contract comes from a service order instead of a job proposal
    job = models.OneToOneField(
        'jobs.Job',
        on_delete=models.PROTECT,
        related_name='contract',
        null=True,
        blank=True,
    )
    proposal = models.OneToOneField(
        'proposals.Proposal',
        on_delete=models.PROTECT,
        related_name='contract',
        null=True,
        blank=True,
    )
    client = models.ForeignKey(
        'clients.ClientProfile',
        on_delete=models.PROTECT,
        related_name='contracts',
    )
    freelancer = models.ForeignKey(
        'freelancers.FreelancerProfile',
        on_delete=models.PROTECT,
        related_name='contracts',
    )

    # --- Terms ---
    agreed_price = models.DecimalField(max_digits=10, decimal_places=2)
    deadline     = models.DateField()

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    # --- Timestamps ---
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Contract #{self.pk} — {self.client} / {self.freelancer}"


class Milestone(models.Model):

    class Status(models.TextChoices):
        PENDING   = 'PENDING',   _('Pending')    # not yet funded by client
        FUNDED    = 'FUNDED',    _('Funded')     # client paid, funds in escrow
        SUBMITTED = 'SUBMITTED', _('Submitted')  # freelancer marked as done
        APPROVED  = 'APPROVED',  _('Approved')   # client confirmed delivery
        DISPUTED  = 'DISPUTED',  _('Disputed')   # client opened a dispute
        RELEASED  = 'RELEASED',  _('Released')   # payout sent to freelancer
        REFUNDED  = 'REFUNDED',  _('Refunded')   # funds returned to client

    contract = models.ForeignKey(
        Contract,
        on_delete=models.CASCADE,
        related_name='milestones',
    )

    # --- Details ---
    title    = models.CharField(max_length=255)
    amount   = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    order    = models.PositiveSmallIntegerField(default=1)  # for ordering multiple milestones

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # --- Timestamps ---
    created_at   = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)  # when freelancer submits
    approved_at  = models.DateTimeField(null=True, blank=True)  # when client approves

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Milestone '{self.title}' — {self.contract}"