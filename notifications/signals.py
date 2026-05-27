
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification
from .services import create_notification


def notify(account, type_, title, message, link=""):
    create_notification(
        account=account,
        notification_type=type_,
        title=title,
        message=message,
        link=link,
    )

# ── Proposals ────────────────────────────────────────────────────────────────

@receiver(post_save, sender='proposals.Proposal')
def on_proposal_saved(sender, instance, created, **kwargs):
    if created:
        # Notify client: new proposal on their job
        client_account = instance.job.client.account
        notify(
            account=client_account,
            type_=Notification.Type.JOB_NEW_PROPOSAL,
            title='New proposal on your job',
            message=f'{instance.freelancer.account.user.username} submitted a proposal on "{instance.job.title}".',
            link=f'/dashboard/jobs',
        )
    else:
        # Notify freelancer: proposal accepted or rejected
        if instance.status == 'ACCEPTED':
            notify(
                account=instance.freelancer.account,
                type_=Notification.Type.PROPOSAL_ACCEPTED,
                title='Your proposal was accepted!',
                message=f'Your proposal on "{instance.job.title}" was accepted. A contract has been created.',
                link=f'/dashboard/contracts',
            )
        elif instance.status == 'REJECTED':
            notify(
                account=instance.freelancer.account,
                type_=Notification.Type.PROPOSAL_REJECTED,
                title='Proposal not selected',
                message=f'Your proposal on "{instance.job.title}" was not selected this time.',
                link=f'/dashboard/proposals',
            )


# ── Contracts ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender='contracts.Contract')
def on_contract_saved(sender, instance, created, **kwargs):
    if created:
        # Notify freelancer: contract started
        notify(
            account=instance.freelancer.account,
            type_=Notification.Type.CONTRACT_STARTED,
            title='Contract started',
            message=f'A new contract has been created for "{instance.job.title if instance.job else "a service order"}".',
            link='/dashboard/contracts',
        )


# ── Milestones ────────────────────────────────────────────────────────────────

@receiver(post_save, sender='contracts.Milestone')
def on_milestone_saved(sender, instance, created, **kwargs):
    if created:
        return

    contract = instance.contract

    if instance.status == 'FUNDED':
        notify(
            account=contract.freelancer.account,
            type_=Notification.Type.PAYMENT_HELD,
            title='Payment is in escrow',
            message=f'The client has funded the milestone "{instance.title}". You can start working.',
            link='/dashboard/contracts',
        )

    elif instance.status == 'SUBMITTED':
        notify(
            account=contract.client.account,
            type_=Notification.Type.MILESTONE_SUBMITTED,
            title='Work submitted for review',
            message=f'"{instance.title}" has been submitted. Review it and approve or dispute.',
            link='/dashboard/contracts',
        )

    elif instance.status == 'APPROVED':
        notify(
            account=contract.freelancer.account,
            type_=Notification.Type.MILESTONE_APPROVED,
            title='Milestone approved!',
            message=f'The client approved "{instance.title}". Your payout is being processed.',
            link='/dashboard/contracts',
        )

    elif instance.status == 'RELEASED':
        notify(
            account=contract.freelancer.account,
            type_=Notification.Type.PAYOUT_SENT,
            title='Payout sent',
            message=f'Your payment for "{instance.title}" has been released.',
            link='/dashboard/contracts',
        )

    elif instance.status == 'DISPUTED':
        # Notify both parties
        notify(
            account=contract.client.account,
            type_=Notification.Type.DISPUTE_OPENED,
            title='Dispute opened',
            message=f'A dispute has been opened for "{instance.title}". The platform will review.',
            link='/dashboard/contracts',
        )
        notify(
            account=contract.freelancer.account,
            type_=Notification.Type.DISPUTE_OPENED,
            title='Dispute opened',
            message=f'The client has opened a dispute for "{instance.title}". The platform will review.',
            link='/dashboard/contracts',
        )


# ── Orders (services) ─────────────────────────────────────────────────────────

@receiver(post_save, sender='services.Order')
def on_order_saved(sender, instance, created, **kwargs):
    if created:
        notify(
            account=instance.service.freelancer.account,
            type_=Notification.Type.CONTRACT_STARTED,
            title='New order received!',
            message=f'{instance.client.account.user.username} ordered your service "{instance.service.title}".',
            link='/dashboard/contracts',
        )

    elif instance.status == 'DELIVERED':
        notify(
            account=instance.client.account,
            type_=Notification.Type.MILESTONE_SUBMITTED,
            title='Order delivered',
            message=f'"{instance.service.title}" has been delivered. Review it and approve.',
            link='/dashboard/services',
        )

    elif instance.status == 'COMPLETED':
        notify(
            account=instance.service.freelancer.account,
            type_=Notification.Type.PAYOUT_SENT,
            title='Order completed',
            message=f'The client approved your delivery for "{instance.service.title}". Payout is on its way.',
            link='/dashboard/contracts',
        )


# ── Reviews ───────────────────────────────────────────────────────────────────

@receiver(post_save, sender='reviews.Review')
def on_review_saved(sender, instance, created, **kwargs):
    if created:
        notify(
            account=instance.reviewee,
            type_=Notification.Type.REVIEW_RECEIVED,
            title='You received a new review',
            message=f'{instance.reviewer.user.username} left you a {instance.rating}★ review.',
            link='/dashboard/profile',
        )


# ── Collabs ───────────────────────────────────────────────────────────────────

@receiver(post_save, sender='collabs.CollabApplication')
def on_collab_application_saved(sender, instance, created, **kwargs):
    if created:
        notify(
            account=instance.collab_post.posted_by.account,
            type_=Notification.Type.COLLAB_APPLICATION,
            title='New collab application',
            message=f'{instance.applicant.account.user.username} applied to your collab "{instance.collab_post.title}".',
            link='/dashboard/collabs',
        )
    elif instance.status == 'ACCEPTED':
        notify(
            account=instance.applicant.account,
            type_=Notification.Type.COLLAB_ACCEPTED,
            title='Collab application accepted!',
            message=f'You were accepted to collaborate on "{instance.collab_post.title}".',
            link='/dashboard/collabs',
        )