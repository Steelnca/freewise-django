
from django.core.management.base import BaseCommand
from django.db import transaction

from contracts.models import Contract, Milestone


class Command(BaseCommand):
    help = "Backfill public IDs for existing contracts and milestones."

    @transaction.atomic
    def handle(self, *args, **options):
        for contract in Contract.objects.filter(public_id__isnull=True).iterator():
            contract.save()

        for milestone in Milestone.objects.filter(public_id__isnull=True).iterator():
            milestone.save()

        self.stdout.write(self.style.SUCCESS("Public IDs backfilled."))