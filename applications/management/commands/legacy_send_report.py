"""Qui émet encore avec le jeton hérité — la condition d'extinction.

La tâche 6 du plan (refuser le jeton hérité à l'émission) ne se décide pas à la
date : elle se décide sur ce que montrent les journaux. Cette commande répond à
la seule question qui compte avant de couper — *reste-t-il quelqu'un derrière ?*

Elle ne modifie rien. C'est délibéré : le geste qui coupe doit rester un geste
humain, pris en connaissance de cause.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from applications.models import Application


class Command(BaseCommand):
    help = (
        "List the applications that still SEND with the legacy app token "
        "(Application.legacy_send_last_used_at). Read-only: it reports the "
        "extinction condition, it never enforces it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--since-days",
            type=int,
            default=None,
            help=(
                "Only count uses more recent than N days. Without it, every "
                "application that has ever sent with the legacy token is listed."
            ),
        )

    def handle(self, *args, **options):
        depuis = options["since_days"]
        queryset = Application.objects.filter(legacy_send_last_used_at__isnull=False)
        if depuis is not None:
            queryset = queryset.filter(
                legacy_send_last_used_at__gte=timezone.now() - timezone.timedelta(days=depuis)
            )
        concernees = list(queryset.order_by("-legacy_send_last_used_at"))

        if not concernees:
            self.stdout.write(
                self.style.SUCCESS(
                    "Aucune application n'émet avec le jeton hérité"
                    + (f" depuis {depuis} jours." if depuis is not None else ".")
                )
            )
            self.stdout.write(
                "Condition d'extinction remplie sur ce critère. Vérifier aussi que "
                "plus aucune page ne porte le bandeau d'avertissement."
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"{len(concernees)} application(s) émettent encore avec le jeton hérité :"
            )
        )
        for app in concernees:
            self.stdout.write(
                f"  #{app.id:<5} {app.name[:40]:<40} "
                f"dernier envoi hérité : {app.legacy_send_last_used_at:%Y-%m-%d %H:%M} "
                f"— propriétaire : {app.owner.email}"
            )
        self.stdout.write(
            "\nCouper le jeton hérité maintenant casserait ces intégrations. "
            "Prévenir ces propriétaires : la console leur montre déjà le bandeau."
        )
