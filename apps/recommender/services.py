"""Turn a filled-in questionnaire into recommendations.

The rules live here rather than in the view, so they can be tested without a
request — the same split the rest of this codebase uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.streaming.models import Anime, Rating, WatchlistEntry

from . import questions as questionnaire
from .model import ModelUnavailable, Query, Scored, TasteModel, explain, load_model, score

logger = logging.getLogger(__name__)

MAX_FREE_TEXT = 300

# How hard a signed-in viewer's own history leans on the result. Small on
# purpose: they came here and answered five questions about tonight, and
# tonight beats their back catalogue.
HISTORY_WEIGHT = 0.3
LIKED_FROM_SCORE = 4


@dataclass(frozen=True)
class Recommendation:
    anilist_id: int
    name: str
    year: int | None
    episodes: int | None
    poster_url: str | None
    # Set only when we carry the title; otherwise the widget says so.
    url: str | None
    available: bool
    fit: float
    reasons: tuple[str, ...]
    neighbours: tuple[str, ...]
    already_seen: bool

    @property
    def match_percent(self) -> int:
        return int(round(self.fit * 100))


def build_query(
    *,
    answers: dict[str, list[str]],
    free_text: str = "",
    user=None,
    model: TasteModel | None = None,
) -> Query:
    """Validate the answers and express them as model terms.

    Unknown questions and unknown options are rejected rather than ignored:
    silently dropping half a query and returning confident recommendations
    for the other half is worse than an error.
    """
    query = Query()

    for key, values in (answers or {}).items():
        question = questionnaire.QUESTIONS_BY_KEY.get(key)
        if question is None:
            raise ValidationError(f"There is no question called {key!r}.")

        chosen = [value for value in (values or []) if value]
        if not chosen:
            if question.optional:
                continue
            raise ValidationError(f"Pick an answer for {question.prompt!r}.")
        if len(chosen) > question.max_choices:
            raise ValidationError(
                f"{question.prompt!r} takes at most {question.max_choices} "
                f"answer{'s' if question.max_choices > 1 else ''}."
            )

        for value in chosen:
            option = questionnaire.option_for(key, value)
            if option is None:
                raise ValidationError(f"{value!r} is not an answer to {question.prompt!r}.")
            # Two moods each count fully; the vector is normalised afterwards,
            # so this is a blend rather than a doubling.
            query.add_terms(option.terms)
            query.add_avoid(option.avoid)
            if option.facet:
                facet, facet_value = option.facet
                query.facets[facet] = facet_value

    text = (free_text or "").strip()[:MAX_FREE_TEXT]
    if text:
        # Reading the sentence needs the model's vocabulary. Without one the
        # text is still recorded, so a caller inspecting the query sees what
        # was asked rather than an empty string.
        query.free_text = text
        if model is not None:
            query.add_free_text(text, model)

    if user is not None and getattr(user, "is_authenticated", False):
        _add_history(query, user=user, model=model)

    if not query.terms and not query.facets and not query.references and not query.avoid:
        if text:
            raise ValidationError(
                "None of those words are ones the model knows. Try a mood, a "
                "genre, or the name of an anime you liked."
            )
        raise ValidationError("Answer at least one question so there is something to go on.")

    return query


def _add_history(query: Query, *, user, model: TasteModel | None) -> None:
    """Fold in what this viewer already told us they like.

    Titles they rated highly nudge the query toward their taste; everything
    they have rated or saved is marked seen, so the widget does not spend a
    slot recommending what is already on their list.

    Keyed on AniList ids, because that is the only identifier the model and
    the catalogue share. A title of ours with no id yet simply does not take
    part — it is not silently mistaken for a different show.
    """
    rated = {
        anilist_id: score
        for anilist_id, score in Rating.objects.filter(user=user)
        .exclude(anime__anilist_id=None)
        .values_list("anime__anilist_id", "score")
    }
    saved = set(
        WatchlistEntry.objects.filter(user=user)
        .exclude(anime__anilist_id=None)
        .values_list("anime__anilist_id", flat=True)
    )
    query.seen_ids = frozenset(rated) | saved

    if model is None:
        return

    liked = {key for key, value in rated.items() if value >= LIKED_FROM_SCORE} | saved
    if not liked:
        return

    by_id = {item.anilist_id: item for item in model.items}
    # Split the weight across their liked titles so somebody with a 40-title
    # list does not drown out the questions they just answered.
    share = HISTORY_WEIGHT / len(liked)
    for anilist_id in liked:
        item = by_id.get(anilist_id)
        if item is None:
            continue
        query.add_terms({term: 1.0 for term in item.terms}, scale=share)


def recommend(
    *,
    answers: dict[str, list[str]],
    free_text: str = "",
    user=None,
    limit: int | None = None,
) -> list[Recommendation]:
    """The whole ask: validate, score, and dress the winners for display."""
    model = load_model()
    query = build_query(answers=answers, free_text=free_text, user=user, model=model)
    count = limit or settings.RECOMMENDER_RESULT_COUNT
    ranked = score(model, query, limit=count)
    if not ranked:
        return []
    return _decorate(ranked)


def _decorate(scored_items: list[Scored]) -> list[Recommendation]:
    """Say, for each ranked title, whether we actually carry it.

    One query against our own catalogue by AniList id. A title we have links
    to its page and uses our artwork; one we do not have is still offered,
    labelled, with the cover art the model shipped — a recommendation you
    cannot watch here is more use than a worse one you can.
    """
    ids = [scored.item.anilist_id for scored in scored_items]
    ours = {
        anime.anilist_id: anime
        for anime in Anime.objects.filter(anilist_id__in=ids)
    }

    recommendations: list[Recommendation] = []
    for scored in scored_items:
        item = scored.item
        anime = ours.get(item.anilist_id)
        recommendations.append(
            Recommendation(
                anilist_id=item.anilist_id,
                name=anime.name if anime else item.name,
                year=item.year,
                episodes=item.episodes,
                poster_url=(anime.poster_url if anime else None) or item.cover or None,
                url=anime.get_absolute_url() if anime else None,
                available=anime is not None,
                fit=scored.fit,
                reasons=tuple(explain(scored)),
                neighbours=item.neighbours[:2],
                already_seen=scored.already_seen,
            )
        )
    return recommendations


def model_status() -> dict:
    """What the widget needs to know before it offers to ask anything."""
    try:
        model = load_model()
    except ModelUnavailable as error:
        logger.warning("Recommender unavailable: %s", error)
        return {"available": False}

    return {
        "available": True,
        "trainedAt": model.trained_at,
        "titles": len(model.items),
        "catalogue": Anime.objects.exclude(anilist_id=None).count(),
        "metrics": model.metrics,
    }
