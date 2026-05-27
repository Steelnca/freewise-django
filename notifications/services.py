
"""
Notification helpers for Freewise.

Keep notification creation centralized so payments, contracts, and moderation
can create consistent messages without duplicating title/message/link logic.
"""

from django.utils.translation import gettext_lazy as _

from django.db import transaction

from .models import Notification
from .pubsub import NotificationHub, serialize_notification


def create_notification(
    *,
    account,
    notification_type: str,
    title: str,
    message: str,
    link: str = "",
):
    """
    Create one notification and publish it to live subscribers after commit.
    """
    if account is None:
        return None

    notification = Notification.objects.create(
        account=account,
        type=notification_type,
        title=title,
        message=message,
        link=link,
    )

    transaction.on_commit(
        lambda: NotificationHub.publish(
            account.id,
            serialize_notification(notification),
        )
    )

    return notification


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