from django.core.management.base import BaseCommand

from applications import inbound_alias
from applications.models import Application


class Command(BaseCommand):
    help = (
        "Regenerate inbound_email_alias into the current format "
        "<name-slug>-<random><INBOUND_EMAIL_ALIAS_SUFFIX> (e.g. mon-app-3f9a2c1b.pushit), "
        "re-provisioning the Exchange alias (deprovision old, provision new). By default "
        "only aliases in NO accepted format are migrated (pre-app_ free-form aliases, "
        "which no longer route). Legacy app_<slug>_<random> aliases still route and are "
        "kept unless --include-app-prefix is passed. The old address stops working once "
        "migrated, so owners must update whatever sends to it. Idempotent; use --dry-run "
        "to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without applying it.",
        )
        parser.add_argument(
            "--include-app-prefix",
            action="store_true",
            help="Also migrate legacy app_<slug>_<random> aliases to the current format.",
        )

    def _needs_migration(self, alias: str, include_app_prefix: bool) -> bool:
        if inbound_alias.is_current_format(alias):
            return False
        if inbound_alias.is_legacy_format(alias):
            return include_app_prefix
        return True

    def handle(self, *args, **options):
        dry = options["dry_run"]
        include_app_prefix = options["include_app_prefix"]
        legacy = [
            app
            for app in Application.objects.order_by("id")
            if self._needs_migration(app.inbound_email_alias, include_app_prefix)
        ]
        if not legacy:
            self.stdout.write("No legacy aliases to migrate.")
            return

        for app in legacy:
            old_local = app.inbound_email_alias
            old_email = app.inbound_email_address
            if dry:
                sample = app.generate_inbound_email_alias(app.name)
                self.stdout.write(
                    f"app {app.id} '{app.name}': {old_local} -> {sample} "
                    "(sample; the real value is assigned on apply)"
                )
                continue
            # Clear the alias so save() reallocates a unique alias+suffix (DB-enforced)
            # and provisions the new Exchange alias; then drop the old Exchange alias.
            app.inbound_email_alias = ""
            app.inbound_email_suffix = ""
            app.save()
            app._deprovision_exchange_alias(old_local)
            self.stdout.write(f"app {app.id} '{app.name}': {old_local} -> {app.inbound_email_alias}")
            self.stdout.write(f"  exchange: -{old_email}  +{app.inbound_email_address}")

        verb = "Would migrate" if dry else "Migrated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(legacy)} application(s)."))
