
"""
Notification helpers for Freewise.

Keep notification creation centralized so payments, contracts, and moderation
can create consistent messages without duplicating title/message/link logic.
"""

from django.utils.translation import gettext_lazy as _

from .models import Notification


def create_notification(
    *,
    account,
    notification_type: str,
    title: str,
    message: str,
    link: str = "",
):
    """
    Create a single notification for one account.

    Returns None when the account is missing so callers can stay simple.
    """
    if account is None:
        return None

    return Notification.objects.create(
        account=account,
        type=notification_type,
        title=title,
        message=message,
        link=link,
    )


def notify_contract_parties(
    *,
    contract,
    notification_type: str,
    title: str,
    message: str,
    link: str = "",
):
    """
    Notify both sides of a contract when something important happens.

    This keeps payments and contract services free from duplicated notification code.
    """
    created = []

    client_account = getattr(getattr(contract, "client", None), "account", None)
    freelancer_account = getattr(getattr(contract, "freelancer", None), "account", None)

    for account in (client_account, freelancer_account):
        notif = create_notification(
            account=account,
            notification_type=notification_type,
            title=title,
            message=message,
            link=link,
        )
        if notif is not None:
            created.append(notif)

    return created