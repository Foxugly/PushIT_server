from .base import *  # noqa: F401,F403


DEBUG = env.bool("DEBUG", default=False)
STATE = "DEV"

# Dev-only tooling (shell_plus, show_urls...): never loaded in prod.
INSTALLED_APPS = [*INSTALLED_APPS, "django_extensions"]  # noqa: F405

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
