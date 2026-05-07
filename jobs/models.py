from django.db import models
from django.utils.translation import gettext_lazy as _


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.CharField(max_length=50, blank=True)  # e.g. icon class name or emoji

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)

    def __str__(self):
        return self.name


class Job(models.Model):

    class Status(models.TextChoices):
        OPEN        = 'OPEN',        _('Open')
        IN_PROGRESS = 'IN_PROGRESS', _('In Progress')
        COMPLETED   = 'COMPLETED',   _('Completed')
        CANCELLED   = 'CANCELLED',   _('Cancelled')

    class ExperienceLevel(models.TextChoices):
        ENTRY    = 'ENTRY',    _('Entry Level')
        MID      = 'MID',      _('Mid Level')
        EXPERT   = 'EXPERT',   _('Expert')

    # --- Ownership ---
    client = models.ForeignKey(
        'clients.ClientProfile',
        on_delete=models.CASCADE,
        related_name='jobs',
    )

    # --- Details ---
    title        = models.CharField(max_length=200)
    description  = models.TextField()
    category     = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        related_name='jobs',
    )
    tags             = models.ManyToManyField(Tag, blank=True, related_name='jobs')
    experience_level = models.CharField(
        max_length=10,
        choices=ExperienceLevel.choices,
        default=ExperienceLevel.MID,
    )

    # --- Budget ---
    budget_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # --- Deadline ---
    deadline = models.DateField(null=True, blank=True)

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title