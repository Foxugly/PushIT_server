"""Back-office staff : rechercher un compte, offrir ou retirer l'accès.

La surface est délibérément étroite. Ces tests fixent surtout ce qui **n'est
pas** modifiable : offrir un accès est un geste commercial, pas une porte
d'entrée vers l'édition des comptes.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models import BypassGrantLog
from billing.service import application_quota, user_is_paid

User = get_user_model()

BILLING_ON = {
    "BILLING_BASE_URL": "https://billing-api.foxugly.com",
    "BILLING_APP_SECRET": "secret-de-test",
    "BILLING_APP_SLUG": "pushit",
}


@pytest.fixture
def staff(db):
    return User.objects.create_superuser(email="staff@foxugly.com", password="pw12345678")


@pytest.fixture
def client_lambda(db):
    return User.objects.create_user(email="client@example.com", password="pw12345678")


def _as(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


# ------------------------------------------------------------------------ accès

@pytest.mark.django_db
def test_an_ordinary_account_cannot_reach_the_staff_api(client_lambda):
    assert _as(client_lambda).get("/api/v1/staff/users/").status_code == 403


@pytest.mark.django_db
def test_an_anonymous_visitor_cannot_reach_the_staff_api(db):
    assert APIClient().get("/api/v1/staff/users/").status_code == 401


@pytest.mark.django_db
def test_an_ordinary_account_cannot_grant_itself_free_access(client_lambda):
    """Le refus doit porter sur l'ecriture aussi, pas seulement sur la lecture."""
    r = _as(client_lambda).patch(
        f"/api/v1/staff/users/{client_lambda.id}/", {"subscription_bypass": True}, format="json"
    )

    client_lambda.refresh_from_db()
    assert r.status_code == 403
    assert client_lambda.subscription_bypass is False


# --------------------------------------------------------------------- recherche

@pytest.mark.django_db
def test_the_bare_listing_shows_who_was_given_free_access(staff, client_lambda):
    """C'est la question que le staff se pose en pratique : à qui ai-je donné quoi ?"""
    User.objects.create_user(email="autre@example.com", password="pw12345678")
    client_lambda.subscription_bypass = True
    client_lambda.save()

    results = _as(staff).get("/api/v1/staff/users/").json()["results"]

    assert [u["email"] for u in results] == ["client@example.com"]


@pytest.mark.django_db
def test_the_search_matches_on_email(staff, client_lambda):
    results = _as(staff).get("/api/v1/staff/users/?q=client@").json()["results"]

    assert [u["email"] for u in results] == ["client@example.com"]


# ------------------------------------------------------------------- la bascule

@pytest.mark.django_db
def test_granting_free_access_records_who_did_it(staff, client_lambda):
    """L'état courant ne dit pas qui a offert l'accès : le journal, si."""
    r = _as(staff).patch(
        f"/api/v1/staff/users/{client_lambda.id}/",
        {"subscription_bypass": True, "bypass_note": "partenaire"},
        format="json",
    )

    client_lambda.refresh_from_db()
    assert r.status_code == 200
    assert client_lambda.subscription_bypass is True
    assert client_lambda.bypass_granted_at is not None

    entree = BypassGrantLog.objects.get()
    assert entree.granted is True
    assert entree.actor_label == "staff@foxugly.com"
    assert entree.target_label == "client@example.com"
    assert entree.note == "partenaire"


@pytest.mark.django_db
def test_revoking_adds_a_line_it_never_erases_one(staff, client_lambda):
    url = f"/api/v1/staff/users/{client_lambda.id}/"
    _as(staff).patch(url, {"subscription_bypass": True}, format="json")
    _as(staff).patch(url, {"subscription_bypass": False}, format="json")

    assert [e.granted for e in BypassGrantLog.objects.order_by("created_at")] == [True, False]


@pytest.mark.django_db
def test_the_grant_date_survives_a_revocation(staff, client_lambda):
    """On garde la trace de l'octroi initial ; le journal porte le reste."""
    url = f"/api/v1/staff/users/{client_lambda.id}/"
    _as(staff).patch(url, {"subscription_bypass": True}, format="json")
    client_lambda.refresh_from_db()
    accorde_le = client_lambda.bypass_granted_at

    _as(staff).patch(url, {"subscription_bypass": False}, format="json")

    client_lambda.refresh_from_db()
    assert client_lambda.bypass_granted_at == accorde_le


@pytest.mark.django_db
def test_editing_only_the_note_writes_no_journal_entry(staff, client_lambda):
    """Le journal trace les bascules d'accès, pas les corrections de frappe."""
    url = f"/api/v1/staff/users/{client_lambda.id}/"
    _as(staff).patch(url, {"subscription_bypass": True, "bypass_note": "a"}, format="json")

    _as(staff).patch(url, {"bypass_note": "b"}, format="json")

    client_lambda.refresh_from_db()
    assert client_lambda.bypass_note == "b"
    assert BypassGrantLog.objects.count() == 1


# ------------------------------------------------------- ce qui n'est PAS ouvert

@pytest.mark.django_db
def test_the_staff_api_cannot_change_an_email(staff, client_lambda):
    """Changer l'email, c'est changer l'identifiant de connexion : ça reste
    l'affaire de l'admin Django, où le geste est tracé par Django lui-même."""
    _as(staff).patch(
        f"/api/v1/staff/users/{client_lambda.id}/", {"email": "vole@example.com"}, format="json"
    )

    client_lambda.refresh_from_db()
    assert client_lambda.email == "client@example.com"


@pytest.mark.django_db
def test_the_staff_api_cannot_deactivate_an_account(staff, client_lambda):
    _as(staff).patch(
        f"/api/v1/staff/users/{client_lambda.id}/", {"is_active": False}, format="json"
    )

    client_lambda.refresh_from_db()
    assert client_lambda.is_active is True


@pytest.mark.django_db
def test_the_staff_api_cannot_promote_anyone_to_staff(staff, client_lambda):
    """Sinon un compte staff compromis se cloner en cascade."""
    _as(staff).patch(
        f"/api/v1/staff/users/{client_lambda.id}/", {"is_staff": True}, format="json"
    )

    client_lambda.refresh_from_db()
    assert client_lambda.is_staff is False


@pytest.mark.django_db
def test_the_staff_api_cannot_delete_an_account(staff, client_lambda):
    r = _as(staff).delete(f"/api/v1/staff/users/{client_lambda.id}/")

    assert r.status_code == 405
    assert User.objects.filter(pk=client_lambda.pk).exists()


@pytest.mark.django_db
def test_the_grant_date_cannot_be_forged_by_the_client(staff, client_lambda):
    """Il est horodaté par le serveur : une date fournie serait un faux."""
    _as(staff).patch(
        f"/api/v1/staff/users/{client_lambda.id}/",
        {"subscription_bypass": True, "bypass_granted_at": "2000-01-01T00:00:00Z"},
        format="json",
    )

    client_lambda.refresh_from_db()
    assert client_lambda.bypass_granted_at.year >= 2026


# ------------------------------------------------------------- effet sur le gating

@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_free_access_opens_the_gate_without_any_subscription(client_lambda):
    assert user_is_paid(client_lambda) is False

    client_lambda.subscription_bypass = True
    client_lambda.save()

    assert user_is_paid(client_lambda) is True
    assert application_quota(client_lambda) > 1


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_free_access_lets_the_account_create_applications(client_lambda):
    """Le but de la manoeuvre : c'est ce geste-la qui doit passer."""
    assert _as(client_lambda).post("/api/v1/apps/", {"name": "A"}, format="json").status_code == 402

    client_lambda.subscription_bypass = True
    client_lambda.save()

    assert _as(client_lambda).post("/api/v1/apps/", {"name": "A"}, format="json").status_code == 201


@pytest.mark.django_db
def test_being_staff_grants_no_business_right_by_itself(staff):
    """Le role d'administration n'ouvre pas la facturation : les deux notions
    doivent rester distinctes."""
    assert staff.subscription_bypass is False


@pytest.mark.django_db
def test_a_plain_staff_account_cannot_grant_free_access(db):
    """`is_staff` ouvre la zone d'administration -- consulter l'etat du backend.
    Offrir un acces gratuit engage de l'argent : il faut etre superuser, et la
    garde Angular exige exactement le meme niveau."""
    simple_staff = User.objects.create_user(
        email="lecture@foxugly.com", password="pw12345678", is_staff=True
    )
    cible = User.objects.create_user(email="cible@example.com", password="pw12345678")

    r = _as(simple_staff).patch(
        f"/api/v1/staff/users/{cible.id}/", {"subscription_bypass": True}, format="json"
    )

    cible.refresh_from_db()
    assert r.status_code == 403
    assert cible.subscription_bypass is False
