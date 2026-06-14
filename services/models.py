from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator

from core.models.mixins import PublicIDMixin

class Service(PublicIDMixin, models.Model):

    class Status(models.TextChoices):
        DRAFT  = 'DRAFT',  _('Draft')
        ACTIVE = 'ACTIVE', _('Active')
        PAUSED = 'PAUSED', _('Paused')

    PUBLIC_ID_PREFIX = "fws"

    freelancer = models.ForeignKey(
        'freelancers.FreelancerProfile',
        on_delete=models.CASCADE,
        related_name='services',
    )
    title       = models.CharField(max_length=200)
    description = models.TextField()
    category    = models.ForeignKey(
        'jobs.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services',
    )
    tags   = models.ManyToManyField('jobs.Tag', blank=True, related_name='services')
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} — {self.freelancer}"


class ServicePackage(PublicIDMixin, models.Model):
    """Each service has up to 3 packages: Basic, Standard, Premium."""

    PUBLIC_ID_PREFIX = "fwsp"

    service       = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='packages')
    title         = models.CharField(max_length=100)   # e.g. Basic / Standard / Premium
    description   = models.TextField(blank=True)
    price         = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    delivery_days = models.PositiveIntegerField()
    revisions     = models.PositiveIntegerField(default=1)
    order         = models.PositiveSmallIntegerField(default=1)  # display order

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.service.title} — {self.title} ({self.price} DZD)"


class Order(PublicIDMixin, models.Model):

    class Status(models.TextChoices):
        PENDING   = 'PENDING',   _('Pending')    # created, awaiting payment
        ACTIVE    = 'ACTIVE',    _('Active')     # paid, freelancer working
        DELIVERED = 'DELIVERED', _('Delivered')  # freelancer submitted work
        COMPLETED = 'COMPLETED', _('Completed')  # client approved
        CANCELLED = 'CANCELLED', _('Cancelled')
        DISPUTED  = 'DISPUTED',  _('Disputed')

    PUBLIC_ID_PREFIX = "fwso"

    service  = models.ForeignKey(Service, on_delete=models.PROTECT, related_name='orders')
    package  = models.ForeignKey(ServicePackage, on_delete=models.PROTECT, related_name='orders')
    client   = models.ForeignKey('clients.ClientProfile', on_delete=models.PROTECT, related_name='orders')

    # Client fills this after ordering — what exactly they need
    requirements = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # Links to the contract/escrow system once payment is made
    contract = models.OneToOneField(
        'contracts.Contract',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order',
    )

    created_at   = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.pk} — {self.service.title} ({self.status})"
