from django.core.management.base import BaseCommand

from payments.reconciliation import reconcile_stale_attempts


class Command(BaseCommand):
    help = "Reconcile stale payment attempts against the configured provider."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=5)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        count = reconcile_stale_attempts(
            minutes=options["minutes"],
            limit=options["limit"],
        )
        self.stdout.write(self.style.SUCCESS(f"Reconciled {count} payment attempts."))