from .base import *  # noqa: F401,F403


DEBUG = env.bool("DEBUG", default=False)
STATE = "DEV"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
