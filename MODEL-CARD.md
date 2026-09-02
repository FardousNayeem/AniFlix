# ANIFLIX recommender — model card

What the "What should I watch?" widget is, what it learned from, how it
learned it, and what it cannot do. Written to be read by somebody who has to
decide whether to trust it, ship it, or retrain it.

Everything here is reproducible from the three commands in **Reproducing it**.

---

## 1. What it does

A viewer answers up to five questions — mood, weight, length, era, anything to
avoid — or types a sentence, and gets a ranked handful of anime with a match
score and the reasons behind it.

It recommends from **everything it was trained on**, not only from what the
site carries. A recommendation we stock links to its page; one we do not is
still shown, labelled *Not available on this site*. Ranking never takes
stock into account, so the best answer is offered whether or not we profit
from it.

**It is not a language model.** It does not understand a sentence. It matches
a bag of words and a set of taps against a learned geometry.

---

## 2. What it was trained on

| | |
| --- | --- |
| Source | [AniList](https://anilist.co) public GraphQL API, `https://graphql.anilist.co` |
| Access | No API key, no account, no authentication |
| Harvested | See `trained_at` in `data/taste_model.json.gz` |
| Fields | Title, genres, community tags with vote rank, synopsis, average score, popularity, episode count, release year, format, studio, cover art and wide banner |
| Signal | The community recommendation graph: crowd-voted "if you liked X, try Y" edges, with net vote counts |
| Ordering | Most popular first, partitioned by release year |

### Why this data

The recommendation graph is the whole point. Genre labels say what a title
*is*; the graph says what people who liked it *actually watched next*. That is
the difference between knowing Psycho-Pass is a sci-fi thriller and knowing
its fans reliably go on to Terror in Resonance. No amount of metadata
recovers that — it only exists because a large number of people voted on it.

Popularity ordering is deliberate. The graph is dense where people watch
things and empty in the long tail, and a model fitted on titles with nine
votes each learns noise. Year partitioning exists only because AniList will
not paginate one sorted query past 5,000 results.

### What was deliberately excluded

- **Adult titles** — `isAdult: false` at the query.
- **Spoiler tags** — AniList flags tags that give away an ending, and
  `Tragedy` is usually one of them. They describe the last episode, not the
  appeal, and would train the model to match on twists the viewer has not
  seen. This is why the questionnaire cannot say "tragic" directly.
- **Rejected recommendations** — edges with a negative net vote are dropped,
  not negated. The crowd disagreeing with a suggestion is weak evidence
  against similarity, not strong evidence.
- **Weakly-voted tags** — below 40% rank a tag is a handful of voters, not a
  description of the show.
- **No personal data of any kind.** No user accounts, watch histories, or
  identifiers were collected from AniList or anywhere else. Every edge is an
  aggregate vote count that AniList publishes openly.

### Terms

AniList's public API is free to use without a key and asks only that clients
identify themselves and respect the rate limit; the harvester sends a
`User-Agent` and paces itself, backing off on `429`. Cover art is served from
AniList's CDN and is downloaded once into local media rather than hotlinked.
The corpus is a build input and is **not** redistributed — `data/` ignores it
in git, and only the trained artifact is committed.

---

## 3. How it was trained

Two stages. Both offline, both deterministic under a fixed seed
(`RANDOM_SEED` in `ml/train.py`).

### Stage 1 — where each title sits

Build the co-occurrence matrix of the recommendation graph, symmetric, with
each edge weighted `log1p(votes)` so a 3,000-vote blockbuster pairing cannot
drown out everything else. Convert it to **PPMI** — positive pointwise mutual
information — and take a **truncated SVD**, folding the singular values in as
`sqrt` and normalising the rows.

That is not a shortcut around word2vec. Levy and Goldberg (NIPS 2014) showed
that skip-gram with negative sampling is *implicitly factorising a shifted
PPMI matrix*, so the SVD reaches the same objective directly — with no
learning rate, no epochs, and the same answer every run.

The SVD itself is the **randomised range finder** of Halko, Martinsson and
Tropp (2011): project onto a small random subspace, sharpen it with four
power iterations because the PPMI spectrum decays slowly, orthonormalise, and
factorise the small matrix that results. A full SVD of a 7,500-title graph
is a 7,500³ job and gigabytes of intermediate; this is seconds.

### Stage 2 — reaching that space with words

Stage 1 only gives vectors to titles that were in the graph. A viewer types
"something dark and psychological, and short". So a **ridge regression** is
fitted from TF-IDF content features onto the stage 1 vectors:

```
W = (XᵀX + λI)⁻¹ XᵀY
```

solved rather than inverted, in float64 because the Gram matrix of a few
thousand sparse-ish columns is ill-conditioned enough for float32 to matter.
`X` is TF-IDF over genres, community tags weighted by vote rank, synopsis
words, title words, studio, release era and episode-count band — sublinear
term frequency, L2-normalised rows. `Y` is the stage 1 embedding.

The result projects *any* bag of words into taste space. That is what makes
the questionnaire and free text work at all.

### Feature weighting

A genre label is a stronger statement about a title than a word that happens
to appear in its synopsis, so features are weighted before TF-IDF: genre 3.0,
tag 2.0 × vote rank, era and length 1.5, title words 2.5, studio 1.0,
synopsis words 1.0.

Synopsis boilerplate ("based on the light novel series") is filtered, but the
same words are **kept in titles** — otherwise "something like Death Note"
loses half the name it is searching for.

---

## 4. How the hyperparameters were chosen

Swept, not guessed. See §5 for what the metrics mean.

| Parameter | Value | Why |
| --- | --- | --- |
| Embedding dimensions | 96 | Peak of the end-to-end metric across {48, 64, 96, 128} |
| Ridge λ | 2.0 | Peak across {0.5, 2, 8, 32} |
| Vocabulary | 14,000 terms | Structured terms first, then description words by document frequency |
| Minimum document frequency | 3 | Below this a term is one show's vocabulary |
| Recommendation edges per title | 25 | AniList's own ordering, best-voted first |

**The trap, recorded so the next person does not fall into it:** the
projection's cosine similarity and R² peak at a *lower* dimension than the
ranking quality does. Optimising either of those picks a worse model. A
vector can be close on average and still order the top ten wrongly, and
ordering the top ten is the entire job. Tune against
`content_recall_at_10`.

---

## 5. How well it works

All figures are on **held-out data the model never saw**, printed by
`manage.py train_recommender` and stored in the artifact under `metrics`.

| Metric | Value | Random baseline |
| --- | --- | --- |
| recall@10 on held-out edges | **0.259** | 0.0013 |
| description → neighbours recall@10 | **0.177** | 0.0013 |
| projection cosine (held-out titles) | 0.552 | — |
| projection R² (held-out titles) | 0.302 | — |

Trained on **9,000** titles, of which **7,473** take part in
the recommendation graph and ship in the artifact. **8,313** edges were
held out. Vocabulary **14,000** terms, **96** dimensions.
Artifact **2.96 MB**. The site carries **309**
of these titles across **36** genres; the rest are offered and labelled
*Not available on this site*.

The recall figures are roughly **193×** and
**132×** better than picking at random.

### What the numbers mean

- **recall@10 on held-out edges** — hide a tenth of the recommendation graph,
  fit on the rest, then check whether the hidden pairings come back as near
  neighbours. This measures stage 1: the geometry.
- **description → neighbours recall@10** — the end-to-end one. Take a title
  the projection was not fitted on, feed only its *content* through the
  regression, and see whether the nearest titles are the ones its fans
  actually watch. This is exactly what the questionnaire does, so it is the
  number that matters.
- **projection cosine / R²** — how close a predicted vector is to the true
  one. Reported for completeness. See the trap in §4.

### What is not measured

Nobody has evaluated this against real ANIFLIX viewers, because there is no
click data to evaluate against. Every number above says the model reproduces
*AniList voters'* judgements. Whether it satisfies this site's audience is
untested, and would need logged outcomes to answer.

---

## 6. How a recommendation is ranked

Three signals, blended, then penalties:

| Signal | Weight | Asks |
| --- | --- | --- |
| `taste` | 0.5 | What do people who like this sort of thing watch? |
| `content` | 0.3 | Does this title literally carry what was asked for? |
| `facet` | 0.2 | Does it fit the stated facts — length, era? |
| `popularity` | +0.12 | Has anyone heard of it? |

The popularity term is deliberately small. It breaks ties between equally
good matches rather than deciding them: without it the long tail wins every
query, because nine thousand obscure OVAs each match some phrasing slightly
better than the famous title the viewer actually meant. With it much higher,
every query returns the same handful of blockbusters.

`taste` is cosine against the learned space. `content` is cosine between the
query's terms and the title's own genres and tags — it exists because the
learned space averages a whole neighbourhood, and on its own it can miss a
title that plainly says what the viewer asked for. `facet` is checked against
real metadata, because "under 16 episodes" is a fact, not a matter of taste.

When no length or era is stated, the facet weight is dropped and the other
two renormalised, so an unstated preference is not scored as a failed test.

Then: each "anything to avoid" term the title carries subtracts 0.45 × its
weight, anything already on the viewer's list subtracts 0.25 — demoted rather
than hidden — and a title the viewer *named* is removed outright, because
somebody asking for "something like Cowboy Bebop" has seen Cowboy Bebop.

The score shown as a percentage is this blend, clamped to 0–100. It is an
absolute measure, not a curve fitted to the results: when nothing matches
well, every number is low and the widget says so rather than dressing up its
best guess.

### Personalisation

Only for signed-in viewers, and only from this site's own database: titles
they rated 4+ or saved nudge the query toward their taste, capped at 0.3 of
the total weight and split across their list, so a long history cannot
overrule the five questions they just answered. Nothing is sent anywhere.

---

## 7. Known limitations

Measured, not assumed. Each was reproduced before being written down.

| It fails at | Example | What happens |
| --- | --- | --- |
| Other languages | `algo oscuro y psicologico` | Refused — English vocabulary only |
| Abstract emotion | `I just went through a breakup` | Very low scores; feelings are not mapped to genres |
| Vague input | `im bored` | Near-arbitrary, honestly scored low |
| Uncommon typos | `comdey` | Missed; correction only fires within 0.84 similarity of a genre or tag name |
| Off-topic text | `how do I cook rice` | Scores something low rather than refusing |

Handled, after being found broken:

- **Negation** — "anything but comedy" once returned comedies. Clauses are now
  split on punctuation and negated ones routed to avoid.
- **Everyday words** — "funny" is not a genre name. A synonym table maps it,
  and ~90 others, onto real terms.
- **Plurals** — training never stemmed, so "pirates" is folded to "pirate" at
  query time.

### Biases worth stating

- **Popularity bias.** The corpus is the most-watched titles, so obscure and
  older work is underrepresented and less likely to be recommended.
- **Community bias.** Every judgement in the model is AniList's userbase —
  skewed young, online, English-speaking and Western. It reflects their taste,
  not a neutral one.
- **English titles.** Matching prefers English and romaji names. A title
  known mainly by another name is harder to reach.
- **Popularity is not quality.** The graph is denser around popular shows, so
  they have better vectors and surface more readily.

---

## 8. What ships, and what does not

`data/taste_model.json.gz` contains the projection matrix, the IDF table, one
vector per title, the metadata each result needs, and the names of the
best-known titles so "something like Cowboy Bebop" resolves to that title's
real position.

Vectors ship as **base64 int8 with one scale factor per row**, not as JSON
numbers. As text the matrices came to 21 MB and took over a second to parse; quantised
the whole artifact is 2.96 MB and loads in 0.17 s. The
largest coordinate error this can introduce is under 0.004, two orders of
magnitude below the gap between adjacent results.

**The running site needs neither numpy nor the network.** Scoring is dot
products over the standard library, and the artifact is read once and cached
until the file changes on disk.

The corpus, the co-occurrence graph and numpy stay behind in the build.

---

## 9. Reproducing it

```bash
pip install -r requirements-ml.txt        # numpy; build only

python manage.py harvest_corpus --by-year   # downloads the corpus (~10 min)
python manage.py seed_catalogue --count 300 # stocks titles, genres and artwork
python manage.py train_recommender          # fits and writes the artifact (~1 min)
```

Deterministic: same corpus in, same artifact out.

`train_recommender` **fails the build** if any questionnaire answer or
free-text synonym names a term the trained vocabulary does not contain. A
renamed AniList tag would otherwise turn an answer into a silent no-op, which
is the worst kind of broken — it still looks like it works.

### When to retrain

- Titles were added to the catalogue and cannot be recommended.
- AniList renamed or retired a tag the questionnaire relies on (the build
  will tell you).
- The corpus is stale enough that recent seasons are missing.

Retraining does not require reseeding, and seeding does not require
retraining — but a title seeded after the last training run is recommended as
one we do not carry until you train again.

---

## 10. Where the code is

| Path | What |
| --- | --- |
| `apps/recommender/ml/harvest.py` | Downloads the corpus. Decides nothing. |
| `apps/recommender/ml/train.py` | Both training stages and the evaluation |
| `apps/recommender/ml/catalogue.py` | Quantises and writes the artifact |
| `apps/recommender/model.py` | Loads it and scores a request. No numpy. |
| `apps/recommender/questions.py` | The questionnaire, and what each answer means |
| `apps/recommender/text.py` | Tokenising and synonyms, shared by both sides |
| `apps/recommender/services.py` | Validation, personalisation, availability |
| `apps/recommender/tests/` | Scoring tests always run; training tests need numpy |
