"""The questions the widget asks, and what each answer means to the model.

This is the one place the product and the model meet. Every option carries the
vocabulary terms it stands for, so an answer is not a magic string handled by
an `if` somewhere: it is a bag of weighted terms projected into the same taste
space as the catalogue.

Terms use the vocabulary built in ``ml/train.py`` — ``genre:``, ``tag:``,
``era:``, ``length:``, ``word:``. ``manage.py train_recommender`` checks every
term here against the trained vocabulary and fails if one is missing, so a
typo cannot quietly become an answer that does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Facets are answered against real metadata rather than the learned space:
# "under 16 episodes" is a fact about a title, not a matter of taste.
FACET_LENGTH = "length"
FACET_ERA = "era"


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    icon: str = ""
    # Vocabulary term -> weight. Weights are relative within an answer.
    terms: dict[str, float] = field(default_factory=dict)
    # Optional hard-ish constraint checked against catalogue metadata.
    facet: tuple[str, str] | None = None
    # Terms that should push a title *down* rather than up.
    avoid: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    options: tuple[Option, ...]
    multiple: bool = False
    max_choices: int = 1
    optional: bool = False
    # Shown under the prompt in the widget.
    hint: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question(
        key="mood",
        prompt="What are you in the mood for?",
        hint="Pick up to two.",
        multiple=True,
        max_choices=2,
        options=(
            Option(
                value="dark",
                label="Something dark and tense",
                icon="ph-mask-sad",
                terms={
                    "genre:thriller": 1.0,
                    "genre:psychological": 1.0,
                    "genre:horror": 0.5,
                    "tag:philosophy": 0.4,
                    "word:dark": 0.4,
                },
            ),
            Option(
                value="action",
                label="Big fights and spectacle",
                icon="ph-sword",
                terms={
                    "genre:action": 1.0,
                    "tag:super-power": 0.7,
                    "tag:martial-arts": 0.5,
                    "word:battle": 0.4,
                    "word:fight": 0.3,
                },
            ),
            Option(
                value="funny",
                label="Something that makes me laugh",
                icon="ph-smiley",
                terms={
                    "genre:comedy": 1.0,
                    "tag:parody": 0.6,
                    "tag:slapstick": 0.5,
                    "tag:satire": 0.5,
                    "word:comedy": 0.4,
                },
            ),
            Option(
                value="emotional",
                label="Something that will wreck me",
                icon="ph-drop",
                terms={
                    "genre:drama": 1.0,
                    "tag:coming-of-age": 0.6,
                    "word:loss": 0.4,
                    "word:death": 0.3,
                },
            ),
            Option(
                value="mystery",
                label="A puzzle to work out",
                icon="ph-magnifying-glass",
                terms={
                    "genre:mystery": 1.0,
                    "tag:detective": 0.7,
                    "genre:psychological": 0.6,
                    "tag:crime": 0.5,
                    "word:mystery": 0.4,
                    "word:truth": 0.3,
                },
            ),
            Option(
                value="calm",
                label="Something easy and warm",
                icon="ph-coffee",
                terms={
                    "genre:slice-of-life": 1.0,
                    "tag:iyashikei": 0.7,
                    "genre:comedy": 0.4,
                    "word:everyday": 0.3,
                },
            ),
        ),
    ),
    Question(
        key="weight",
        prompt="How heavy do you want it?",
        options=(
            Option(
                value="light",
                label="Light — I want to switch off",
                icon="ph-feather",
                terms={
                    "tag:episodic": 0.6,
                    "genre:comedy": 0.5,
                    "genre:slice-of-life": 0.4,
                    "tag:slapstick": 0.3,
                },
            ),
            Option(
                value="balanced",
                label="Somewhere in the middle",
                icon="ph-scales",
                terms={},
            ),
            Option(
                value="heavy",
                label="Heavy — I want to feel something",
                icon="ph-anchor",
                terms={
                    "genre:drama": 0.6,
                    "genre:psychological": 0.5,
                    "tag:philosophy": 0.5,
                    "word:death": 0.3,
                },
            ),
        ),
    ),
    Question(
        key="length",
        prompt="How much time have you got?",
        options=(
            Option(
                value="short",
                label="An evening or two",
                icon="ph-hourglass-simple",
                terms={"length:short": 1.0},
                facet=(FACET_LENGTH, "short"),
            ),
            Option(
                value="season",
                label="A full season",
                icon="ph-television-simple",
                terms={"length:season": 1.0},
                facet=(FACET_LENGTH, "season"),
            ),
            Option(
                value="long",
                label="Give me something to live in",
                icon="ph-infinity",
                terms={"length:long": 1.0},
                facet=(FACET_LENGTH, "long"),
            ),
            Option(value="any", label="No preference", icon="ph-dots-three", terms={}),
        ),
    ),
    Question(
        key="era",
        prompt="Old-school or modern?",
        options=(
            Option(
                value="classic",
                label="Classics, before 2000",
                icon="ph-cassette-tape",
                terms={"era:classic": 1.0},
                facet=(FACET_ERA, "classic"),
            ),
            Option(
                value="recent",
                label="Something recent",
                icon="ph-sparkle",
                terms={"era:recent": 1.0},
                facet=(FACET_ERA, "recent"),
            ),
            Option(value="any", label="Either is fine", icon="ph-dots-three", terms={}),
        ),
    ),
    Question(
        key="avoid",
        prompt="Anything you would rather avoid?",
        hint="Optional. Pick as many as you like.",
        multiple=True,
        max_choices=4,
        optional=True,
        options=(
            Option(
                value="gore",
                label="Gore and body horror",
                icon="ph-drop-half",
                avoid={"tag:gore": 1.0, "genre:horror": 0.8, "tag:body-horror": 1.0},
            ),
            Option(
                value="romance",
                label="Romance",
                icon="ph-heart-break",
                avoid={"genre:romance": 1.0, "tag:love-triangle": 0.6},
            ),
            Option(
                value="ecchi",
                label="Fan service",
                icon="ph-eye-closed",
                avoid={"genre:ecchi": 1.0, "tag:nudity": 0.8, "tag:female-harem": 0.5},
            ),
            Option(
                value="sad",
                label="Anything bleak",
                icon="ph-sun",
                avoid={
                    "genre:psychological": 0.6,
                    "genre:horror": 0.5,
                    "tag:gore": 0.5,
                    "tag:war": 0.4,
                },
            ),
        ),
    ),
)

QUESTIONS_BY_KEY = {question.key: question for question in QUESTIONS}


def option_for(question_key: str, value: str) -> Option | None:
    question = QUESTIONS_BY_KEY.get(question_key)
    if question is None:
        return None
    for option in question.options:
        if option.value == value:
            return option
    return None


def all_terms() -> set[str]:
    """Every vocabulary term an answer can produce, tapped or typed.

    ``train_recommender`` uses this to prove the model can hear every answer.
    The free-text synonyms are included because they are answers too — a
    viewer typing "scary" is choosing horror as surely as tapping it.
    """
    from .text import TEXT_ALIASES

    terms: set[str] = set(TEXT_ALIASES.values())
    for question in QUESTIONS:
        for option in question.options:
            terms.update(option.terms)
            terms.update(option.avoid)
    return terms


def as_payload() -> list[dict]:
    """The questionnaire as the browser widget consumes it."""
    return [
        {
            "key": question.key,
            "prompt": question.prompt,
            "hint": question.hint,
            "multiple": question.multiple,
            "maxChoices": question.max_choices,
            "optional": question.optional,
            "options": [
                {"value": option.value, "label": option.label, "icon": option.icon}
                for option in question.options
            ],
        }
        for question in QUESTIONS
    ]
