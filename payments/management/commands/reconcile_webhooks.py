
"""
Freewise management command for replaying stored webhook logs.

Use this to safely retry:
- verified webhook logs
- unprocessed webhook logs
- logs that failed after signature verification

This is useful during development and later for operator recovery.
"""

from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _

from payments.models import WebhookLog
from payments.webhooks import PaymentWebhookError, reconcile_chargily_webhook_log


class Command(BaseCommand):
    help = _("Replay unprocessed Chargily webhook logs safely.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            default="chargily",
            help=_("Webhook provider name to reconcile."),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help=_("Maximum number of logs to process."),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=_("Show what would be processed without changing anything."),
        )

    def handle(self, *args, **options):
        provider = options["provider"]
        limit = options["limit"]
        dry_run = options["dry_run"]

        qs = (
            WebhookLog.objects.filter(
                provider_name=provider,
                signature_valid=True,
                processed=False,
            )
            .exclude(status=WebhookLog.Status.IGNORED)
            .order_by("created_at")
        )

        if limit:
            qs = qs[:limit]

        processed = 0
        failed = 0
        skipped = 0

        for log in qs:
            if dry_run:
                self.stdout.write(
                    self.style.NOTICE(
                        f"[DRY RUN] would reconcile log #{log.pk} "
                        f"({log.provider_name} / {log.event_name} / {log.provider_event_id})"
                    )
                )
                skipped += 1
                continue

            try:
                reconcile_chargily_webhook_log(webhook_log=log)
                processed += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Reconciled log #{log.pk} ({log.provider_event_id})"
                    )
                )
            except PaymentWebhookError as exc:
                failed += 1
                log.status = WebhookLog.Status.FAILED
                log.processing_error = str(exc)
                log.save(
                    update_fields=["status", "processing_error", "updated_at"]
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipped log #{log.pk} ({log.provider_event_id}): {exc}"
                    )
                )
            except Exception as exc:
                failed += 1
                log.status = WebhookLog.Status.FAILED
                log.processing_error = str(exc)
                log.save(
                    update_fields=["status", "processing_error", "updated_at"]
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"Failed log #{log.pk} ({log.provider_event_id}): {exc}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed={processed}, failed={failed}, dry_run={dry_run}, skipped={skipped}"
            )
        )