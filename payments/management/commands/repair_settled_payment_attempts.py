
from django.core.management.base import BaseCommand
from django.db import transaction

from payments.models import PaymentAttempt
from payments.services import _sync_funding_state_after_settlement


class Command(BaseCommand):
    help = (
        "Repairs milestones and contracts for already-settled "
        "payment attempts."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        repaired = 0

        attempts = (
            PaymentAttempt.objects
            .filter(
                internal_status=PaymentAttempt.InternalStatus.SETTLED
            )
            .select_related(
                "milestone",
                "contract",
            )
            .order_by("id")
        )

        total = attempts.count()

        self.stdout.write(
            self.style.NOTICE(
                f"Found {total} settled payment attempts."
            )
        )

        for attempt in attempts:
            try:
                _sync_funding_state_after_settlement(
                    milestone=attempt.milestone,
                )

                repaired += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        (
                            f"✓ Attempt {attempt.attempt_id} "
                            f"(Milestone #{attempt.milestone_id}) repaired."
                        )
                    )
                )

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(
                        (
                            f"✗ Attempt {attempt.attempt_id} "
                            f"failed: {exc}"
                        )
                    )
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Finished. "
                    f"Repaired {repaired}/{total} settled attempts."
                )
            )
        )