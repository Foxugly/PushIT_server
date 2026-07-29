"""Le rapport qui conditionne l'extinction.

Il ne modifie rien, et c'est ce qu'on vérifie en premier : le geste qui coupe
doit rester humain.
"""
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.models import User
from applications.models import Application


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="proprio@example.com", password="1234Test!!")


def _application(owner, name, legacy_at=None):
    application = Application(owner=owner, name=name)
    application.set_new_app_token()
    application.save()
    if legacy_at is not None:
        Application.objects.filter(pk=application.pk).update(legacy_send_last_used_at=legacy_at)
        application.refresh_from_db()
    return application


def _run(**kwargs):
    sortie = StringIO()
    call_command("legacy_send_report", stdout=sortie, **kwargs)
    return sortie.getvalue()


@pytest.mark.django_db
def test_says_the_condition_is_met_when_nobody_sends_with_the_legacy_token(owner):
    _application(owner, "Propre")

    assert "Aucune application" in _run()


@pytest.mark.django_db
def test_names_the_applications_that_would_break(owner):
    """Le rapport sert a prevenir, donc il nomme l'application ET son proprietaire."""
    _application(owner, "Encore heritee", legacy_at=timezone.now())

    sortie = _run()

    assert "Encore heritee" in sortie
    assert owner.email in sortie


@pytest.mark.django_db
def test_the_window_excludes_an_old_use(owner):
    """Un envoi herite d'il y a six mois ne dit pas qu'on casserait quelqu'un
    aujourd'hui."""
    _application(owner, "Ancienne", legacy_at=timezone.now() - timezone.timedelta(days=180))

    assert "Aucune application" in _run(since_days=30)
    assert "Ancienne" in _run()


@pytest.mark.django_db
def test_the_report_changes_nothing(owner):
    application = _application(owner, "Intacte", legacy_at=timezone.now())
    avant = (application.legacy_send_last_used_at, application.app_token_hash, application.is_active)

    _run()

    application.refresh_from_db()
    assert (
        application.legacy_send_last_used_at,
        application.app_token_hash,
        application.is_active,
    ) == avant
