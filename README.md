# PushIT Server

Backend Django/DRF pour la gestion d'applications, devices et notifications push.

Le projet expose :
- une authentification utilisateur JWT
- une authentification applicative via `X-App-Token`
- la gestion des applications et des devices
- la création et l'envoi de notifications
- une documentation OpenAPI
- des health checks, logs structurés et métriques Prometheus

## État du projet

Le backend est aujourd'hui un MVP sérieux, mais pas encore une stack prod complète.

Points importants :
- la base de développement et de test reste SQLite
- le provider push FCM est encore mocké
- les flows principaux, l'idempotence, le wiring, le schéma OpenAPI et plusieurs cas de concurrence sont couverts par les tests
- des briques d'observabilité de base sont en place : `health`, `request_id`, `incident_id`, logs JSON, métriques Prometheus, alerting et dashboard Grafana

## Stack

- Python 3.14
- Django 5
- Django REST Framework
- SimpleJWT
- Celery
- SQLite en dev/test actuel
- drf-spectacular pour OpenAPI

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
REDIS_URL=redis://127.0.0.1:6379/0
JWT_ACCESS_MINUTES=30
JWT_REFRESH_DAYS=7
FCM_API_KEY=None
METRICS_AUTH_TOKEN=
```

Variables utiles :
- `STATE=DEV|TEST|PROD`
- `SQLITE_NAME` pour surcharger le chemin de la base SQLite
- `METRICS_AUTH_TOKEN` pour protéger `/health/metrics/`
- `REDIS_URL` pour Celery

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
4. créer une notification
5. mettre la notification en file d'envoi

### Flow applicatif

1. créer ou régénérer un `app_token`
2. lier un device via `/api/v1/devices/link/`
3. créer une notification via `/api/v1/notifications/app/create/`

## Script de démo

Le script [scripts/full_flow.py](C:/Users/rvilain/PycharmProjects/PushIT_server/scripts/full_flow.py) enchaîne le flow nominal complet :
- création du compte
- login
- création de l'application
- liaison du device
- création de la notification
- mise en file d'envoi

Exécution :

```powershell
.\.venv\Scripts\python.exe scripts/full_flow.py
```

Base URL personnalisée :

```powershell
$env:PUSHIT_BASE_URL="http://127.0.0.1:8000/api/v1"
.\.venv\Scripts\python.exe scripts/full_flow.py
```

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
