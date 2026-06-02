
from django.core.management.base import BaseCommand

from payments.reconciliation import (
    reconcile_pending_attempts,
)


class Command(BaseCommand):

    help = "Reconcile unresolved payment attempts."

    def handle(self, *args, **kwargs):

        count = reconcile_pending_attempts()

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {count} payment attempts."
            )
        )