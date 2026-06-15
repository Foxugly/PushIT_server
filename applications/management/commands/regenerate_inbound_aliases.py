from django.core.management.base import BaseCommand

from applications.models import Application


class Command(BaseCommand):
    help = (
        "Regenerate inbound_email_alias for applications still on the legacy format "
        "(no 'app_' prefix) into app_<name-slug>_<random>, re-provisioning the Exchange "
        "alias (deprovision old, provision new). Idempotent — already-migrated apps are "
        "skipped. Use --dry-run to preview without changing anything."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without applying it.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        legacy = [
            app
            for app in Application.objects.all()
            if not app.inbound_email_alias.startswith(Application.ALIAS_PREFIX)
        ]
        if not legacy:
            self.stdout.write("No legacy aliases to migrate.")
            return

        for app in legacy:
            old = app.inbound_email_alias
            new = app.generate_inbound_email_alias(app.name)
            while Application.objects.filter(inbound_email_alias=new).exists():
                new = app.generate_inbound_email_alias(app.name)
            self.stdout.write(f"app {app.id} '{app.name}': {old} -> {new}")
            if dry:
                continue
            old_email = app.inbound_email_address  # old@domain, before the field changes
            app.inbound_email_alias = new
            app.save(update_fields=["inbound_email_alias"])
            # Best-effort Exchange swap (no-op when unconfigured; never raises).
            app._deprovision_exchange_alias(old)
            app._provision_exchange_alias()
            self.stdout.write(f"  exchange: -{old_email}  +{app.inbound_email_address}")

        verb = "Would migrate" if dry else "Migrated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(legacy)} application(s)."))
