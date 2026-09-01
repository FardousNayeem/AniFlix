"""Test settings: fast hashing, in-memory database, no external calls."""

from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

DATABASES["default"]["NAME"] = ":memory:"  # noqa: F405

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"  # noqa: F405

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PAYMENT_GATEWAY = "dummy"
