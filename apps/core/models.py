"""Abstract building blocks reused by the product apps."""

from django.db import models


class TimeStampedModel(models.Model):
    """Adds creation/update bookkeeping without repeating it in every app."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
