"""JSON endpoints for the recommendation widget.

Two routes, both cheap. The questionnaire is served from the same definitions
the scorer uses, so the browser cannot ask a question the model has never
heard of.
"""

from __future__ import annotations

import json
import logging

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import questions as questionnaire
from . import services
from .model import ModelUnavailable

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 8 * 1024


@require_GET
@ensure_csrf_cookie
def questions_view(request):
    """The questions to ask, and whether there is a model to answer with.

    This also plants the CSRF cookie. The widget rides on every page, and a
    page that renders no form — the homepage, a genre listing — leaves Django
    with no reason to set one, so the first ask would be rejected. Fetching
    the questions always happens first, which makes this the one place the
    cookie is guaranteed to be set before it is needed.
    """
    status = services.model_status()
    return JsonResponse(
        {
            "available": status["available"],
            "questions": questionnaire.as_payload() if status["available"] else [],
            "trainedAt": status.get("trainedAt", ""),
            "titles": status.get("titles", 0),
        }
    )


@require_POST
def recommend_view(request):
    """Score one filled-in questionnaire.

    Open to anonymous visitors: recommending is the whole point of the widget
    and a sign-in wall would kill it. Signing in only adds personalisation.
    """
    if len(request.body) > MAX_BODY_BYTES:
        return JsonResponse(
            {"ok": False, "message": "That was too much to read. Shorten it and try again."},
            status=413,
        )

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"ok": False, "message": "Malformed request."}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "message": "Malformed request."}, status=400)

    answers = payload.get("answers") or {}
    if not isinstance(answers, dict) or not all(
        isinstance(value, list) for value in answers.values()
    ):
        return JsonResponse(
            {"ok": False, "message": "Answers must be a list per question."}, status=400
        )

    free_text = payload.get("text") or ""
    if not isinstance(free_text, str):
        return JsonResponse({"ok": False, "message": "Malformed request."}, status=400)

    try:
        recommendations = services.recommend(
            answers={key: [str(value) for value in values] for key, values in answers.items()},
            free_text=free_text,
            user=request.user,
        )
    except ValidationError as error:
        return JsonResponse({"ok": False, "message": " ".join(error.messages)}, status=400)
    except ModelUnavailable:
        # Logged where it is raised. The widget hides itself on this.
        return JsonResponse(
            {
                "ok": False,
                "message": "The recommender is not trained on this server yet.",
            },
            status=503,
        )

    return JsonResponse(
        {
            "ok": True,
            "results": [
                {
                    "id": item.anilist_id,
                    "name": item.name,
                    "year": item.year,
                    "episodes": item.episodes,
                    "poster": item.poster_url,
                    "url": item.url,
                    "available": item.available,
                    "match": item.match_percent,
                    "reasons": list(item.reasons),
                    "neighbours": list(item.neighbours),
                    "alreadySeen": item.already_seen,
                }
                for item in recommendations
            ],
        }
    )
