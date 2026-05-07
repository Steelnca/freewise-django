from django.db import models
from django.utils.translation import gettext_lazy as _


class CollabPost(models.Model):

    class Status(models.TextChoices):
        OPEN   = 'OPEN',   _('Open')
        CLOSED = 'CLOSED', _('Closed')

    posted_by = models.ForeignKey(
        'freelancers.FreelancerProfile',
        on_delete=models.CASCADE,
        related_name='collab_posts',
    )

    # --- Details ---
    title         = models.CharField(max_length=200)
    description   = models.TextField()
    skills_needed = models.ManyToManyField(
        'freelancers.Skill',
        blank=True,
        related_name='collab_posts',
    )
    spots         = models.PositiveSmallIntegerField(default=1, help_text="How many collaborators are needed")  # how many collaborators needed

    # --- Status ---
    status = models.CharField(
        max_length=10,
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
        return f"Collab: {self.title} by {self.posted_by}"


class CollabApplication(models.Model):

    class Status(models.TextChoices):
        PENDING  = 'PENDING',  _('Pending')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        REJECTED = 'REJECTED', _('Rejected')

    collab_post = models.ForeignKey(
        CollabPost,
        on_delete=models.CASCADE,
        related_name='applications',
    )
    applicant = models.ForeignKey(
        'freelancers.FreelancerProfile',
        on_delete=models.CASCADE,
        related_name='collab_applications',
    )

    message = models.TextField()
    status  = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('collab_post', 'applicant')

    def __str__(self):
        return f"{self.applicant} → {self.collab_post} ({self.status})"


class CollabMember(models.Model):
    collab_post      = models.ForeignKey(
        CollabPost,
        on_delete=models.CASCADE,
        related_name='members',
    )
    freelancer       = models.ForeignKey(
        'freelancers.FreelancerProfile',
        on_delete=models.CASCADE,
        related_name='collab_memberships',
    )
    role_description = models.CharField(max_length=200, blank=True)
    joined_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('collab_post', 'freelancer')

    def __str__(self):
        return f"{self.freelancer} in {self.collab_post}"