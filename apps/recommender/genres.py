"""The genres ANIFLIX offers, and how AniList's vocabulary maps onto them.

Kept as data rather than as rows created ad hoc during an import, so the
genre list is something you can read and change in one place instead of
discovering it by querying the database.

Every genre here is created whether or not a title currently sits in it. An
empty genre page is not a bug: it is a shelf waiting for stock, and it tells
a visitor what the site intends to carry.
"""

from __future__ import annotations

# name -> the one-line description shown on the genre page.
GENRES: dict[str, str] = {
    # AniList's own genre set. These arrive on almost every title.
    "Action": "Fights, chases and set pieces, with the plot carried by movement.",
    "Adventure": "A journey outward: new places, new people, a long way from home.",
    "Comedy": "Written to be funny, whether that is wordplay or a man hitting a wall.",
    "Drama": "The weight is emotional, and the story trusts you to sit with it.",
    "Ecchi": "Suggestive by design. Not explicit, but not subtle either.",
    "Fantasy": "Magic is real and the rules of the world say so.",
    "Horror": "Built to frighten, from slow dread to something behind you.",
    "Mahou Shoujo": "Ordinary girls, extraordinary transformations.",
    "Mecha": "Giant machines, piloted, and usually about the people inside them.",
    "Music": "Bands, idols and performance, where the songs carry the story.",
    "Mystery": "A question is posed early and the answer is earned late.",
    "Psychological": "The tension is in someone's head, and it does not let up.",
    "Romance": "Two people, and whether they manage it.",
    "Sci-Fi": "Technology and its consequences, taken seriously.",
    "Slice of Life": "Ordinary days, closely observed. Nothing explodes.",
    "Sports": "Training, rivalry and the match itself.",
    "Supernatural": "Ghosts, spirits and things the world cannot quite explain.",
    "Thriller": "Momentum and threat, with the tension kept tight.",
    # Demographics. The audience a title was written for, which says as much
    # about its tone as its genre does.
    "Shounen": "Aimed at teenage boys: growth, rivalry and escalation.",
    "Shoujo": "Aimed at teenage girls: feeling, relationships and interiority.",
    "Seinen": "Aimed at adult men, and usually darker or slower for it.",
    "Josei": "Aimed at adult women, with romance written for grown-ups.",
    # Themes strong enough to browse by.
    "Isekai": "Somebody from our world wakes up in another one.",
    "Super Power": "Abilities beyond the ordinary, and what they cost.",
    "Suspense": "The dread of what is about to happen, held as long as possible.",
    "Martial Arts": "Disciplined combat, trained for and fought properly.",
    "Military": "Armies, wars and the chains of command inside them.",
    "Historical": "Set in a real past, and interested in getting it right.",
    "School": "Classrooms, clubs and the years that shape everyone in them.",
    "Iyashikei": "Deliberately soothing. Watched to feel better afterwards.",
    "Parody": "Affectionate mockery of the things it loves.",
    "Detective": "Somebody works it out, on the page, in front of you.",
    "Post-Apocalyptic": "After the collapse, among what is left.",
    "Cyberpunk": "High technology, low life, neon and rain.",
    "Space": "Out there: ships, colonies and the distance between them.",
    "Time Travel": "Cause and effect, rearranged.",
}

# AniList tag -> our genre. AniList files these as tags rather than genres, but
# they are exactly what somebody browses by, so they are promoted here.
TAG_TO_GENRE: dict[str, str] = {
    "Shounen": "Shounen",
    "Shoujo": "Shoujo",
    "Seinen": "Seinen",
    "Josei": "Josei",
    "Isekai": "Isekai",
    "Super Power": "Super Power",
    "Martial Arts": "Martial Arts",
    "Military": "Military",
    "Historical": "Historical",
    "School": "School",
    "Iyashikei": "Iyashikei",
    "Parody": "Parody",
    "Detective": "Detective",
    "Post-Apocalyptic": "Post-Apocalyptic",
    "Cyberpunk": "Cyberpunk",
    "Space": "Space",
    "Time Manipulation": "Time Travel",
    "Time Loop": "Time Travel",
    "Time Skip": "Time Travel",
    "Crime": "Suspense",
    "Philosophy": "Psychological",
}

# A tag has to be voted this strongly before it earns a genre. Below it the
# tag is a detail somebody noticed, not what the title is.
TAG_GENRE_MIN_RANK = 60
