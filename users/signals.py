
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

User = get_user_model()


@receiver(post_save, sender=User)
def create_account(sender, instance, created, **kwargs):
    """Auto-create an Account whenever a new User is registered."""
    if created:
        from accounts.models import Account
        Account.objects.get_or_create(user=instance)