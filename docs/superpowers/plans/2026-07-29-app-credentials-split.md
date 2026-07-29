# Séparation des identifiants d'application — plan d'implémentation

> **Pour un agent :** exécuter tâche par tâche. Les étapes sont en cases à cocher (`- [ ]`).

**But :** séparer le code d'enrôlement (partagé, public) du jeton d'émission (secret, relisible), pour qu'un destinataire ne détienne plus la capacité d'émettre.

**Architecture :** l'`Application` gagne un `enrolment_code` en clair, rotatable, qui remplace le jeton dans le QR. Un nouveau modèle `AppSendToken` porte des jetons d'émission **multiples et nommés**, authentifiés par empreinte et **relisibles** via un chiffré séparé. Le jeton historique reste accepté aux deux endroits pendant la transition, puis est refusé à l'émission.

**Pile :** Django 6 / DRF côté serveur, Angular 21 côté console, Kotlin Multiplatform côté mobile.

---

## Le défaut corrigé

Aujourd'hui un seul jeton fait deux métiers opposés :

| | Preuve |
|---|---|
| il est **distribué** à chaque destinataire | `devices/serializers.py:59`, `TokenStorage.android.kt:92`, `DeviceLinkManager.kt:86` |
| il **autorise l'émission** | `notifications/api_views_app_token.py:206` (`AppTokenAuthentication`) |

Conséquence : quiconque a scanné le QR peut écrire à tous les terminaux de l'application. Le hachage côté serveur ne protège rien, puisque la valeur est volontairement partagée.

## Contraintes globales

- ~~**L'application mobile publiée ne doit pas casser.** `QrScannerScreen.kt:64` stocke la chaîne scannée verbatim et la poste à `/devices/link/` : une installation existante acceptera donc un code d'enrôlement **sans mise à jour**.~~ **FAUX, corrigé en tâche 5 (2026-07-29).** La ligne 64 stocke bien verbatim, mais la **ligne 63** filtrait `startsWith("apt_")` **avant** de stocker : tout QR `apk_` était refusé avec « QR code invalide ». Le mobile n'était pas transparent, c'était le maillon manquant. Le scanner accepte désormais `apk_` et `apt_`. Reste vrai : ne pas introduire de QR structuré (JSON, URL), qui briserait la lecture verbatim.
- **Le chemin d'authentification reste fondé sur une empreinte.** Le chiffré ne sert **qu'à** la révélation. Un défaut dans la fonction de révélation ne doit pas pouvoir affaiblir l'authentification.
- **Un code d'enrôlement n'authentifie jamais une émission.** C'est l'invariant que tout le plan protège ; il mérite un test dédié dans chaque phase.
- **Un jeton d'émission ne lie jamais un terminal.** Sinon on le redistribuerait aux téléphones et on recréerait le défaut.
- La rotation actuelle **ne délie pas** les terminaux déjà rattachés (`DeviceLinkManager.kt:96`) ; elle casse seulement les rattachements futurs. Ne pas décrire la migration comme une reconnexion de masse : c'est faux.
- Formats : `apk_` + 12 caractères base62 (~16) pour l'enrôlement, `apt_` + 22 caractères base62 (~26, 128 bits) pour l'émission. Préfixe stocké en clair ramené à **8** caractères.

## Matrice de compatibilité

| Identifiant | `/devices/link/` | `/notifications/app/*` |
|---|:--:|:--:|
| jeton historique | accepté (P1→P4) | accepté (P1→P3), **refusé en P4** |
| code d'enrôlement `apk_` | accepté | **jamais** |
| jeton d'émission `apt_` | **jamais** | accepté |

## Structure des fichiers

**`PushIT_server`**
- Modifier `applications/models.py` — `enrolment_code`, `enrolment_code_rotated_at` ; `app_token_*` marqués hérités.
- Créer `applications/models_send_token.py` — `AppSendToken`, `SendTokenReveal` (journal).
- Créer `applications/crypto.py` — `MultiFernet`, clés depuis SSM.
- Modifier `applications/authentication.py` — résolution par empreinte sur `AppSendToken`, repli hérité.
- Créer `applications/api_views_send_tokens.py` — CRUD + révélation.
- Modifier `applications/api_views.py` — expulsion d'un terminal (tâche 3).
- Modifier `devices/api_views_app_token.py` — accepter le code d'enrôlement.
- Migrations : ajout de champs + génération d'un code pour chaque application existante.

**`PushIT_frontend`** — page `application-detail` : bloc enrôlement (QR + code visible), bloc jetons d'émission (liste, création, révélation, révocation), onglet exemples 6 langages.

**`PushIT_app`** — aucun changement fonctionnel requis. Renommages de confort en P3.

---

## Tâche 1 : le code d'enrôlement (serveur)

**Fichiers :** `applications/models.py`, migration, `devices/api_views_app_token.py`, `applications/tests/test_enrolment_code.py`

**Interfaces produites :** `Application.enrolment_code` (str, `apk_…`), `Application.rotate_enrolment_code() -> str`.

- [x] **Étape 1 : écrire le test qui échoue**

```python
@pytest.mark.django_db
def test_a_device_links_with_the_enrolment_code(app, client):
    """C'est le remplacement du jeton dans le QR."""
    r = client.post("/api/v1/devices/link/", {
        "app_token": app.enrolment_code, "device_name": "Pixel",
        "platform": "android", "push_token": "fcm_x",
    }, format="json")
    assert r.status_code == 201


@pytest.mark.django_db
def test_the_enrolment_code_can_never_send(app, client):
    """L'invariant central : le code est public, il ne doit ouvrir aucune emission."""
    r = client.post("/api/v1/notifications/app/send/", {"title": "x", "message": "y"},
                    format="json", HTTP_X_APP_TOKEN=app.enrolment_code)
    assert r.status_code == 401
```

- [x] **Étape 2 : vérifier l'échec** — `pytest applications/tests/test_enrolment_code.py -v`, attendu : `enrolment_code` inexistant.
- [x] **Étape 3 : ajouter le champ et le générateur**

```python
def generate_enrolment_code() -> str:
    # Public : partage dans le QR. Court, sans ambiguite visuelle.
    return f"apk_{secrets.token_urlsafe(9)[:12]}"
```

- [x] **Étape 4 : migration de données** — attribuer un code à chaque application existante (`RunPython`, boucle avec `bulk_update`).
- [x] **Étape 5 : accepter le code dans `/devices/link/` et `/devices/unlink/`**, en gardant le jeton hérité.
- [x] **Étape 6 : vérifier le passage** des deux tests.
- [x] **Étape 7 : commit** — `feat(apps): code d'enrolement distinct du jeton`.

## Tâche 2 : les jetons d'émission (serveur)

**Fichiers :** `applications/models_send_token.py`, `applications/crypto.py`, `applications/authentication.py`, `applications/api_views_send_tokens.py`, tests.

**Interfaces produites :** `AppSendToken.issue(application, name) -> (instance, raw)`, `AppSendToken.reveal() -> str`.

- [x] **Étape 1 : le modèle**

```python
class AppSendToken(models.Model):
    """Jeton d'emission, multiple et nomme.

    Deux representations volontairement distinctes :
    - `token_hash`, unique et indexe, sert a l'AUTHENTIFICATION ;
    - `secret_encrypted`, chiffre, sert UNIQUEMENT a la revelation.

    L'authentification ne dechiffre jamais : un defaut dans la revelation ne
    peut donc pas affaiblir le controle d'acces. Perdre la cle de chiffrement
    rend les jetons illisibles mais TOUJOURS fonctionnels -- c'est le bon sens
    de la panne.
    """
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="send_tokens")
    name = models.CharField(max_length=60)
    prefix = models.CharField(max_length=12, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    secret_encrypted = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
```

- [x] **Étape 2 : le chiffrement, à deux clés dès le premier jour**

`applications/crypto.py`, **`MultiFernet`** et non `Fernet` : `APP_TOKEN_ENCRYPTION_KEYS` est une **liste** de clés, la première chiffre, toutes déchiffrent.

```python
def cipher() -> MultiFernet:
    """Plusieurs cles, la premiere chiffre et toutes dechiffrent.

    Prevu des le depart : avec une cle unique, la faire tourner un jour rendrait
    d'un coup TOUS les jetons illisibles. On se piegerait soi-meme, et au pire
    moment -- celui ou l'on change une cle est rarement un moment calme.
    Rotation = poser la nouvelle en tete, re-chiffrer, retirer l'ancienne.
    """
```

Si aucune clé n'est configurée : la création de jetons fonctionne, la révélation renvoie 503 explicite. **Ne jamais retomber silencieusement sur du clair.**
- [x] **Étape 3 : tests d'authentification**

```python
@pytest.mark.django_db
def test_a_send_token_authenticates_an_emission(app): ...

@pytest.mark.django_db
def test_a_revoked_send_token_is_refused(app):
    """La revocation doit etre immediate : c'est le geste d'urgence."""

@pytest.mark.django_db
def test_a_send_token_can_never_link_a_device(app):
    """L'inverse de l'invariant : sinon on le redistribuerait aux telephones."""

@pytest.mark.django_db
def test_revealing_requires_the_password_and_is_logged(staff_client, app): ...
```

- [x] **Étape 4 : brancher `AppTokenAuthentication`** — chercher d'abord dans `AppSendToken`, puis retomber sur le jeton hérité (journalisé sous `legacy_app_token_send`, pour savoir quand on peut couper).
- [x] **Étape 5 : l'API** — `GET/POST /apps/<id>/send-tokens/`, `DELETE …/<id>/`, `POST …/<id>/reveal/` (mot de passe requis, écrit `SendTokenReveal`).
- [x] **Étape 6 : vérifier**, puis **commit**.

## Tâche 3 : expulser un abonné (serveur + console)

**Manque avéré, sans rapport direct avec la séparation mais révélé par elle.** Aucune route ne permet aujourd'hui au propriétaire de délier le terminal d'un tiers : `DeviceUnlinkWithAppTokenApiView` délie le terminal *de l'appelant*, et `DeviceUnlinkByApplicationApiView` sert au destinataire qui se désabonne. Et changer le code d'enrôlement **ne délie personne**.

Conséquence : un indésirable rattaché ne peut être retiré qu'en désactivant l'application entière. Ce trou devient plus visible avec un code d'enrôlement fait pour circuler.

**Fichiers :** `applications/api_views.py`, `applications/api_urls.py`, tests, console.

- [x] **Étape 1 : le test qui échoue**

```python
@pytest.mark.django_db
def test_the_owner_can_evict_a_linked_device(owner_client, app, foreign_device):
    """Changer le code d'enrolement ne delie personne : sans cette route, un
    indesirable ne se retire qu'en desactivant toute l'application."""
    r = owner_client.delete(f"/api/v1/apps/{app.id}/devices/{foreign_device.id}/")

    assert r.status_code == 204
    assert not app.device_links.filter(device=foreign_device, is_active=True).exists()


@pytest.mark.django_db
def test_evicting_touches_only_this_application(owner_client, app, other_app, foreign_device):
    """Le terminal peut etre rattache a plusieurs applications : en expulser d'une
    ne doit pas le couper des autres, qui ne regardent pas ce proprietaire."""


@pytest.mark.django_db
def test_a_stranger_cannot_evict_from_an_application_they_do_not_own(client, app, foreign_device):
    assert client.delete(f"/api/v1/apps/{app.id}/devices/{foreign_device.id}/").status_code in (403, 404)
```

- [x] **Étape 2 : vérifier l'échec**, puis implémenter `DELETE /apps/<id>/devices/<device_id>/` (désactive le lien, ne supprime pas le terminal — il appartient à quelqu'un d'autre).
- [x] **Étape 3 : console** — bouton « Retirer » sur la liste des terminaux de l'application, avec confirmation.
- [x] **Étape 4 : vérifier**, puis **commit**.

## Tâche 4 : la console

- [x] Bloc **Enrôlement** : code affiché en permanence, QR, bouton « Nouveau code » avec avertissement — *les terminaux déjà rattachés ne sont pas touchés ; pour retirer quelqu'un, utiliser la liste des terminaux*.
- [x] Bloc **Jetons d'émission** : liste (nom, préfixe, dernier usage), création avec révélation unique, bouton « Révéler » derrière la re-saisie du mot de passe, révocation.
- [x] **La révélation se masque d'elle-même** après quelques secondes, et n'est jamais rendue par défaut. Un jeton laissé à l'écran finit dans une capture ou un partage d'écran.
- [x] **Avertissement d'extinction** : si l'application émet encore avec le jeton hérité (drapeau posé par le journal `legacy_app_token_send`), un bandeau le dit sur sa page. Sans ça, l'extinction de la tâche 6 casse l'intégration de quelqu'un sans prévenir.
- [x] Onglet **Exemples** : C, C++, Python, Java, Ruby, Go.
- [x] **Les exemples lisent le jeton depuis une variable d'environnement** (`PUSHIT_TOKEN`), jamais en dur. C'est ce que font les bonnes documentations, et ça évite le geste qui fait fuiter les secrets : coller l'extrait tel quel dans un dépôt. L'identifiant d'application, lui, peut être écrit dans l'exemple — il n'est pas secret.
- [x] **Les exemples visent le jeton d'émission, jamais le code d'enrôlement.** Un exemple qui se trompe réintroduit la faille par la documentation.
- [x] Copie dans les 5 catalogues ; la spec de parité doit passer.

## Tâche 5 : l'application mobile — **pas du confort**

~~Aucun changement fonctionnel : le scanner stocke la chaîne verbatim et le serveur accepte les deux formats.~~ Le scanner filtrait `apt_` avant de stocker : sans cette tâche, **tout QR produit par la tâche 4 aurait été refusé** par l'application. Voir « Contraintes globales ».

- [x] Accepter `apk_` (et garder `apt_`) dans le scanner — extrait dans `looksLikeEnrolmentCode`, testé.
- [x] Renommer `app_token` en `enrolment_code` dans `TokenStorage` (migration de la valeur existante, Android + iOS).
- [x] Reformuler les libellés et messages d'erreur (FR/NL/EN).
- [ ] Publier — l'app n'est pas encore sur le Play Store (publication en pause), donc rien à pousser dans l'immédiat. À reprendre avec le dossier Play.

## Tâche 6 : extinction — **EN ATTENTE, volontairement**

Elle ne se décide pas à la date mais sur ce que montrent les journaux. Rien n'est coupé tant que la première case n'est pas vraie.

- [ ] Attendre que plus aucune application n'émette avec le jeton hérité **et** qu'aucune page ne porte le bandeau d'avertissement.
      → `python manage.py legacy_send_report [--since-days N]` répond exactement à cette question (lecture seule, il ne coupe rien).
- [ ] Refuser le jeton hérité sur `/notifications/app/*` ; le garder sur `/devices/link/` — les installations mobiles s'en servent encore pour s'enrôler.
- [ ] Supprimer `app_token_hash` / `app_token_prefix` après une période d'observation.

---

## État au 2026-07-29

| Tâche | État |
|---|---|
| 1 — code d'enrôlement (serveur) | livré, PR #21 |
| 2 — jetons d'émission (serveur) | livré, PR #22 |
| 3 — expulser un abonné | livré, serveur #23 + console #38 |
| 4 — console | serveur #24 livré, console #39 |
| 5 — mobile | livré, app #4 (non publié) |
| 6 — extinction | **en attente** de la condition ci-dessus |

**Geste ops encore dû :** poser `APP_TOKEN_ENCRYPTION_KEYS` dans `/pushit/prod` (SSM, `SecureString`, liste séparée par des virgules). Sans lui, les jetons d'émission fonctionnent mais la console répond 503 à « Revoir le jeton » — c'est le comportement voulu, pas une panne, mais la relecture reste impossible.

---

## Ce qui reste à décider avant de coder

1. **`APP_TOKEN_ENCRYPTION_KEYS` en SSM** — nouvelle variable, `SecureString`, liste de clés, à documenter dans `OPERATIONS.md`. Sa perte rend les jetons illisibles mais **pas inopérants** : l'authentification passe par l'empreinte.
2. **Combien de jetons par application ?** Une borne (10 ?) évite qu'un compte compromis en fabrique mille.
3. **Durée de la phase 5** avant extinction — dépend de ce que montrent les journaux.
4. **Détection d'un vol** — aujourd'hui rien ne signale un usage anormal ; `last_used_at` par jeton aide à trier, mais ne prévient pas. Hors périmètre, à garder en tête.
