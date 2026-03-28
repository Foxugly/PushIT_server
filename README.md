# PushIT Server

Backend Django/DRF pour la gestion d'applications, devices et notifications push.

Le projet expose :
- une authentification utilisateur JWT
- une authentification applicative via `X-App-Token`
- la gestion des applications et des devices
- la création et l'envoi de notifications
- l'ingestion d'emails entrants via une boite IMAP catch-all
- une documentation OpenAPI
- des health checks, logs structurés et métriques Prometheus
- des notifications planifiées et des périodes blanches par application

## État du projet

Le backend est aujourd'hui un MVP sérieux, mais pas encore une stack prod complète.

Points importants :
- la base de développement et de test reste SQLite
- le provider push FCM est encore mocké
- les flows principaux, l'idempotence, le wiring, le schéma OpenAPI et plusieurs cas de concurrence sont couverts par les tests
- des briques d'observabilité de base sont en place : `health`, `request_id`, `incident_id`, logs JSON, métriques Prometheus, alerting et dashboard Grafana

## Stack

- Python 3.14
- Django 6.0.3
- Django REST Framework
- SimpleJWT
- Celery
- SQLite en dev/test actuel
- drf-spectacular pour OpenAPI
- prometheus-client

## Structure rapide

- [accounts](C:/Users/rvilain/PycharmProjects/PushIT_server/accounts) : auth utilisateur, JWT, profil courant
- [applications](C:/Users/rvilain/PycharmProjects/PushIT_server/applications) : applications, app tokens, permissions associées
- [devices](C:/Users/rvilain/PycharmProjects/PushIT_server/devices) : devices et liaison via app token
- [notifications](C:/Users/rvilain/PycharmProjects/PushIT_server/notifications) : notifications, queue, services d'envoi, tâches Celery
- [health](C:/Users/rvilain/PycharmProjects/PushIT_server/health) : live, ready, metrics
- [config](C:/Users/rvilain/PycharmProjects/PushIT_server/config) : settings, middleware, erreurs API, logging, métriques
- [tests](C:/Users/rvilain/PycharmProjects/PushIT_server/tests) : intégration, schéma OpenAPI, wiring, gestion des exceptions
- [observability](C:/Users/rvilain/PycharmProjects/PushIT_server/observability) : Prometheus, alerting, Grafana

## Installation

### 1. Créer l'environnement

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configurer l'environnement

Copier `.env_template` vers `.env` puis ajuster les variables utiles.

Exemple minimal :

```env
DJANGO_SECRET_KEY=django-insecure-change-me
STATE=DEV
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=http://localhost:4200,http://127.0.0.1:4200
REDIS_URL=redis://127.0.0.1:6379/0
JWT_ACCESS_MINUTES=30
JWT_REFRESH_DAYS=7
FCM_API_KEY=None
METRICS_AUTH_TOKEN=
```

Variables utiles :
- `STATE=DEV|TEST|PROD`
- `SQLITE_NAME` pour surcharger le chemin de la base SQLite
- `CORS_ALLOWED_ORIGINS` pour autoriser le frontend local ou un domaine frontend donne
- `METRICS_AUTH_TOKEN` pour protéger `/health/metrics/`
- `REDIS_URL` pour Celery
- `INBOUND_EMAIL_DOMAIN` pour le domaine email entrant, par exemple `pushit.com`
- `INBOUND_EMAIL_IMAP_ENABLED` pour activer la surveillance IMAP
- `INBOUND_EMAIL_IMAP_HOST`, `INBOUND_EMAIL_IMAP_PORT`, `INBOUND_EMAIL_IMAP_USERNAME`, `INBOUND_EMAIL_IMAP_PASSWORD`, `INBOUND_EMAIL_IMAP_FOLDER`
- `PROMETHEUS_MULTIPROC_DIR` pour agréger correctement les métriques en multi-worker

En `PROD`, `DJANGO_SECRET_KEY` et `ALLOWED_HOSTS` sont désormais obligatoires. Le démarrage échoue si ces variables ne sont pas définies explicitement.

### 3. Migrer la base

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

### 4. Lancer le serveur

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

API locale :
- `http://127.0.0.1:8000/api/v1/`
- schéma : `http://127.0.0.1:8000/api/schema/`
- Swagger : `http://127.0.0.1:8000/api/docs/`
- Redoc : `http://127.0.0.1:8000/api/redoc/`

## Settings

Les settings sont séparés dans [config/settings](C:/Users/rvilain/PycharmProjects/PushIT_server/config/settings) :
- [base.py](C:/Users/rvilain/PycharmProjects/PushIT_server/config/settings/base.py)
- [dev.py](C:/Users/rvilain/PycharmProjects/PushIT_server/config/settings/dev.py)
- [test.py](C:/Users/rvilain/PycharmProjects/PushIT_server/config/settings/test.py)
- [prod.py](C:/Users/rvilain/PycharmProjects/PushIT_server/config/settings/prod.py)

`DJANGO_SETTINGS_MODULE=config.settings` reste l'entrée standard. Le choix `dev/test/prod` est piloté par `DJANGO_ENV` ou `STATE`.

## Authentification

### Utilisateur

- login JWT via `/api/v1/auth/login/`
- accès Bearer sur les endpoints utilisateur

### Application

- auth applicative via header `X-App-Token`
- l'auth app n'utilise pas `request.user = owner`
- l'application authentifiée est disponible via `request.auth_application`

## Flows principaux

### Flow utilisateur

1. créer un compte
2. se connecter
3. créer une application
4. définir éventuellement des périodes blanches sur l'application
5. créer une notification immédiate ou planifiée
6. lister / modifier / supprimer une notification future si nécessaire
7. mettre la notification en file d'envoi

### Flow applicatif

1. créer ou régénérer un `app_token`
2. lier un device via `/api/v1/devices/link/`
3. créer une notification immédiate ou planifiée via `/api/v1/notifications/app/create/`

### Flow email entrant

1. configurer une boite mail catch-all sur le domaine, par exemple `*@pushit.com`
2. configurer le backend avec les accès IMAP de cette boite
3. un worker Celery interroge périodiquement la boite IMAP
4. pour chaque mail non lu :
5. le backend lit l'expéditeur
6. le backend lit le destinataire et extrait le préfixe avant `@`
7. le préfixe doit correspondre à `app_token_prefix`
8. l'expéditeur doit correspondre à un utilisateur existant
9. l'application ciblée doit appartenir à cet utilisateur
10. le sujet devient le titre de la notification
11. le contenu texte devient le message
12. un marqueur `[SEND_AT:2026-03-28T20:00:00+01:00]` dans le sujet permet de planifier l'envoi

## Planification et périodes blanches

Une notification peut maintenant porter un champ `scheduled_for` :
- absent ou `null` : notification immédiate classique
- datetime futur : notification placée en statut `scheduled`

Les réponses de lecture exposent aussi `effective_scheduled_for` :
- `scheduled_for` = date demandée par le client
- `effective_scheduled_for` = prochaine date effective d'envoi calculée à partir des périodes blanches actuellement configurées
- créer ou modifier une période blanche ne réécrit pas rétroactivement `scheduled_for`, mais peut faire évoluer `effective_scheduled_for`

Les notifications futures utilisateur sont exposées via :
- `GET /api/v1/notifications/future/`
- `GET /api/v1/notifications/future/{id}/`
- `PATCH /api/v1/notifications/future/{id}/`
- `DELETE /api/v1/notifications/future/{id}/`

Le listing `GET /api/v1/notifications/future/` accepte aussi :
- `effective_scheduled_from`
- `effective_scheduled_to`
- `has_quiet_period_shift=true|false`
- `ordering=effective_scheduled_for`
- `ordering=-effective_scheduled_for`

Ces filtres s'appliquent à `effective_scheduled_for`, pas à la valeur brute `scheduled_for`.

Le listing `GET /api/v1/notifications/app/` accepte les mêmes filtres et le même tri côté app token, avec en plus `status`.

Le listing `GET /api/v1/notifications/` accepte :
- `application_id`
- `status`
- `effective_scheduled_from`
- `effective_scheduled_to`
- `has_quiet_period_shift=true|false`
- `ordering=effective_scheduled_for`
- `ordering=-effective_scheduled_for`

### Query params notifications

| Endpoint | Paramètre | Type | Description |
| --- | --- | --- | --- |
| `/api/v1/notifications/` | `application_id` | integer | Filtre par application |
| `/api/v1/notifications/` | `status` | string | Filtre par statut |
| `/api/v1/notifications/` | `effective_scheduled_from` | datetime | Borne minimale sur `effective_scheduled_for` |
| `/api/v1/notifications/` | `effective_scheduled_to` | datetime | Borne maximale sur `effective_scheduled_for` |
| `/api/v1/notifications/` | `has_quiet_period_shift` | boolean | Garde uniquement les notifications décalées ou non par une période blanche |
| `/api/v1/notifications/` | `ordering` | string | `effective_scheduled_for` ou `-effective_scheduled_for` |
| `/api/v1/notifications/future/` | `effective_scheduled_from` | datetime | Borne minimale sur `effective_scheduled_for` |
| `/api/v1/notifications/future/` | `effective_scheduled_to` | datetime | Borne maximale sur `effective_scheduled_for` |
| `/api/v1/notifications/future/` | `has_quiet_period_shift` | boolean | Garde uniquement les notifications futures décalées ou non |
| `/api/v1/notifications/future/` | `ordering` | string | `effective_scheduled_for` ou `-effective_scheduled_for` |
| `/api/v1/notifications/app/` | `status` | string | Filtre par statut côté app token |
| `/api/v1/notifications/app/` | `effective_scheduled_from` | datetime | Borne minimale sur `effective_scheduled_for` |
| `/api/v1/notifications/app/` | `effective_scheduled_to` | datetime | Borne maximale sur `effective_scheduled_for` |
| `/api/v1/notifications/app/` | `has_quiet_period_shift` | boolean | Garde uniquement les notifications décalées ou non |
| `/api/v1/notifications/app/` | `ordering` | string | `effective_scheduled_for` ou `-effective_scheduled_for` |

Les périodes blanches sont gérées par application via :
- `GET /api/v1/apps/{app_id}/quiet-periods/`
- `POST /api/v1/apps/{app_id}/quiet-periods/`
- `GET /api/v1/apps/{app_id}/quiet-periods/{quiet_period_id}/`
- `PATCH /api/v1/apps/{app_id}/quiet-periods/{quiet_period_id}/`
- `DELETE /api/v1/apps/{app_id}/quiet-periods/{quiet_period_id}/`

Si une notification doit partir pendant une période blanche active, l'envoi est reporté automatiquement à la fin de cette période.
Ce report est appliqué au moment du dispatch/envoi. Il n'y a pas de réécriture immédiate de `scheduled_for` lors de la création d'une période blanche après coup.

## Script de démo

Le script [scripts/full_flow.py](C:/Users/rvilain/PycharmProjects/PushIT_server/scripts/full_flow.py) enchaîne le flow nominal complet :
- création du compte
- login
- création de l'application
- liaison du device
- création, listing, modification et suppression d'une période blanche
- création, listing, lecture, modification et suppression d'une notification planifiée
- démonstration des filtres de listing `application_id`, `status`, `effective_scheduled_*`, `has_quiet_period_shift` et `ordering`
- création d'une notification immédiate
- mise en file d'envoi de la notification immédiate

Exemples de requêtes avancées montrées dans le script :
- `GET /api/v1/notifications/?application_id=<id>&status=scheduled&has_quiet_period_shift=true&ordering=-effective_scheduled_for`
- `GET /api/v1/notifications/future/?effective_scheduled_from=<iso>&effective_scheduled_to=<iso>`
- `GET /api/v1/notifications/future/?ordering=-effective_scheduled_for`
- `GET /api/v1/notifications/app/?status=scheduled&has_quiet_period_shift=true&ordering=-effective_scheduled_for`

Exécution :

```powershell
.\.venv\Scripts\python.exe scripts/full_flow.py
```

Base URL personnalisée :

```powershell
$env:PUSHIT_BASE_URL="http://127.0.0.1:8000/api/v1"
.\.venv\Scripts\python.exe scripts/full_flow.py
```

## Workflow frontend

Le Swagger expose désormais les endpoints, l'auth et des exemples de payloads métier. Pour le séquencement concret côté frontend, voir aussi [docs/frontend-workflows.md](C:/Users/rvilain/PycharmProjects/PushIT_server/docs/frontend-workflows.md).

## Tests

### Lancer toute la suite

```powershell
.\.venv\Scripts\pytest.exe -q
```

### Suites utiles

```powershell
.\.venv\Scripts\pytest.exe tests/test_full_flow_integration.py -q
.\.venv\Scripts\pytest.exe tests/test_openapi_schema.py -q
.\.venv\Scripts\pytest.exe tests/test_configuration_wiring.py -q
.\.venv\Scripts\pytest.exe tests/test_exception_handling.py -q
```

Exemples par domaine :

```powershell
.\.venv\Scripts\pytest.exe accounts/tests -q
.\.venv\Scripts\pytest.exe applications/tests -q
.\.venv\Scripts\pytest.exe devices/tests -q
.\.venv\Scripts\pytest.exe notifications/tests -q
```

## Observabilité

### Endpoints de santé

- `GET /health/live/`
- `GET /health/ready/`
- `GET /health/metrics/`

### Logs

Les logs sont structurés JSON avec notamment :
- `request_id`
- `incident_id`
- `error_code`
- `application_id`
- `notification_id`
- `device_id`

Une erreur non gérée renvoie un `500` avec :

```json
{
  "code": "internal_error",
  "detail": "Internal server error.",
  "incident_id": "inc_xxxxxxxxxxxx"
}
```

### Métriques

Exemples exposés :
- `pushit_http_requests_total`
- `pushit_process_uptime_seconds`
- `pushit_app_token_auth_total`
- `pushit_notification_send_total`
- `pushit_notification_delivery_total`
- `pushit_notifications_total`
- `pushit_devices_total`

## Alerting et dashboard

Un socle d'observabilité prêt à lancer est fourni dans [observability](C:/Users/rvilain/PycharmProjects/PushIT_server/observability) avec :
- Prometheus
- règles d'alerting
- Grafana
- un dashboard backend pré-provisionné

Lancement :

```powershell
docker compose -f docker-compose.observability.yml up -d
```

Accès :
- Prometheus : `http://localhost:9090`
- Grafana : `http://localhost:3001`

Le dashboard principal est :
- `PushIT Backend Overview`

Documentation dédiée :
- [observability/README.md](C:/Users/rvilain/PycharmProjects/PushIT_server/observability/README.md)

## Contrat d'erreur API

Les erreurs simples utilisent :

```json
{
  "code": "some_error_code",
  "detail": "Readable message"
}
```

Les erreurs de validation utilisent :

```json
{
  "code": "validation_error",
  "detail": "Validation error.",
  "errors": {
    "field_name": [
      "..."
    ]
  }
}
```

## Limitations connues

- SQLite reste utilisé en dev/test, donc certains comportements de concurrence diffèrent d'un PostgreSQL réel
- FCM est mocké, donc le comportement provider réseau réel n'est pas encore validé
- Celery est exécuté en mode eager en `DEV` et `TEST`
- `drf-spectacular` émet encore un warning de dépréciation sous Python 3.14

## Prochaines étapes recommandées

- valider les flows critiques sous PostgreSQL
- exécuter Celery avec vrai broker et vrai worker séparé
- brancher un provider push réel ou un stub réseau plus réaliste
- ajouter centralisation des logs et alertes effectives vers un canal externe

## Licence

Projet sous licence MIT. Voir [LICENSE](C:/Users/rvilain/PycharmProjects/PushIT_server/LICENSE).
