"""Local development settings: loud errors, no HTTPS assumptions."""

from .base import *  # noqa: F401,F403
from config.env import env_list

DEBUG = True

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "0.0.0.0", "testserver"],
)

CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:8000", "http://127.0.0.1:8000"],
)

# ManifestStaticFilesStorage requires collectstatic; unhelpful during development.
STORAGES["staticfiles"]["BACKEND"] = "whitenoise.storage.CompressedStaticFilesStorage"  # noqa: F405

INTERNAL_IPS = ["127.0.0.1"]
