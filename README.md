# ANIFLIX

Anime streaming, fan events and a merch store, in one Django project.

Three product areas share one account and one design system:

| Area          | What it does                                                        |
| ------------- | ------------------------------------------------------------------- |
| **Streaming** | Catalogue, genre pages, episode player, ratings, My List, comments   |
| **Events**    | Published events, seat capacity, one-click registration, tickets     |
| **Shop**      | Merch catalogue, cart, checkout, payment gateway, orders, receipts   |

---

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# paste that into DJANGO_SECRET_KEY in .env

python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000>. The admin is at `/admin/`.

**Activate the virtualenv first.** Without it there is no `python` on the path
on most Linux systems (only `python3`), and Django is not installed globally.
`source .venv/bin/activate` puts both on the path; `deactivate` removes them.

To skip activating, call the interpreter directly:

```bash
.venv/bin/python manage.py runserver
```

---

## Layout

```
config/                 settings (base/dev/prod/test), root URLs, WSGI/ASGI
  env.py                typed reader for environment variables
apps/
  core/                 shared abstractions: TimeStamped model, pagination,
                        template tags, error pages, the legacy importer
  accounts/             the User model, profile, allauth adapters
  streaming/            genres, anime, episodes, ratings, watchlist, comments
    video.py            turns a stored link into something a browser can play
  events/               venues, events, registrations
  shop/                 products, cart, orders, receipts
    payments/           gateway interface + dummy and SSLCommerz backends
  recommender/          the "what should I watch" widget and its model
    questions.py        the questionnaire, and what each answer means
    model.py            loads the trained artifact and scores a request
    text.py             tokenising, shared by training and the request path
    ml/                 the build step: harvest, train, export (needs numpy)
data/                   the trained model artifact, committed
templates/              project templates; app folders mirror the app names
static/css/             tokens -> base -> components -> layout -> pages
static/js/app.js        all front-end behaviour, no framework
static/js/recommender.js  the widget, kept out of app.js
```

### The rule every app follows

```
views  ->  selectors.py   (reads: querysets, annotations, 404s)
       ->  services.py    (writes: every state change, every invariant)
       ->  models.py      (shape and database-level constraints)
```

A view validates input with a form, calls exactly one service or selector, and
renders. It does not build querysets inline and it does not enforce rules. That
is why the same invariant cannot be enforced two different ways in two
different places, and why the rules are testable without a request.

---

## Configuration

Every setting comes from the environment. `.env.example` lists all of them with
comments; nothing has a hardcoded secret.

| Variable                                       | Notes                                             |
| ---------------------------------------------- | ------------------------------------------------- |
| `DJANGO_SECRET_KEY`                            | Required. The app refuses to start without it.    |
| `DJANGO_SETTINGS_MODULE`                       | `config.settings.dev` / `.prod` / `.test`         |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`           | Google sign-in. Blank disables the button.        |
| `PAYMENT_GATEWAY`                              | `dummy` (default, no network) or `sslcommerz`     |
| `SSLCOMMERZ_STORE_ID` / `_STORE_PASSWORD`      | Required only when the gateway is `sslcommerz`    |
| `CART_MAX_QUANTITY_PER_ITEM`                   | Per-line cap enforced in the cart service         |
| `RECOMMENDER_DATA_DIR`                         | Where the trained model lives. Defaults to `data/` |
| `RECOMMENDER_RESULT_COUNT`                     | Titles returned per ask (default 3)               |

### Google sign-in

1. Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID.
2. Authorised redirect URI: `http://127.0.0.1:8000/accounts/google/login/callback/`
3. Put the client id and secret in `.env`. Nothing needs to be added in the
   admin: the provider is configured from settings.

### Payments

`PAYMENT_GATEWAY=dummy` skips the network entirely and bounces straight back to
the success URL, so checkout can be exercised end to end without credentials.
It is the only safe choice for local work.

For SSLCommerz, set the store id and password and leave `SSLCOMMERZ_SANDBOX=true`
until you are ready. Two endpoints matter:

- `/shop/pay/<ref>/return/` is where the customer's browser comes back.
- `/shop/pay/<ref>/ipn/` is the server-to-server notification, and it is the one
  that guarantees a paid order is recorded even if the customer closes the tab.

Neither trusts its payload. Both re-verify against the gateway's validation API
and compare the settled amount to the order total before anything is marked paid.

---

## The recommender

A floating widget on every page asks five questions — or takes a sentence —
and picks from the catalogue. It is a trained model, not a call to somebody
else's recommendation API and not a pile of `if genre == "action"`.

### How it learns

Training happens offline, in two stages.

**Stage 1 — where titles sit.** `harvest_corpus` downloads public anime
metadata and the community "if you liked this, try that" graph from AniList,
which needs no key. Training builds the PPMI matrix of that graph and takes
its truncated SVD, giving every title a 96-dimension vector. Levy and
Goldberg (2014) showed skip-gram with negative sampling is implicitly
factorising a shifted PPMI matrix, so this reaches the same objective
directly — deterministically, and without a learning rate to tune. Titles
that people watch together end up close together, whatever their genre
labels say.

**Stage 2 — reaching that space with words.** Those vectors only exist for
titles in the graph, but a viewer types "something dark and psychological".
So a ridge regression is fitted from TF-IDF content features (genres, tags,
description, era, length, title words) onto the stage 1 vectors. The result
projects *any* bag of words — a questionnaire answer, a typed sentence —
into the same space.

The catalogue's own nine titles are matched to their AniList entries and
inherit their learned vectors, so the site recommends from real crowd
behaviour rather than from nine rows of local metadata.

### What ships

`data/taste_model.json.gz` — the projection matrix, the IDF table, one vector
per catalogue title, and the names of ~1,200 well-known titles so "something
like Cowboy Bebop" resolves to that title's actual position. **The running
site needs neither numpy nor the network**: scoring is dot products over the
standard library, and the artifact is read once and cached until it changes.

### How it ranks

Three signals, blended:

| signal    | asks                                              | comes from                |
| --------- | ------------------------------------------------- | ------------------------- |
| `taste`   | what do people who like this sort of thing watch? | the learned space         |
| `content` | does this title literally carry what was asked?   | its own genres and tags   |
| `facet`   | does it fit the stated facts?                     | episode count, year       |

Then answers under "anything to avoid" subtract, and anything already on the
viewer's list is demoted. Signing in is optional; it only adds
personalisation.

### Retraining

```bash
pip install -r requirements-ml.txt      # numpy, build only
python manage.py harvest_corpus         # ~3 min, writes data/anilist_corpus.json.gz
python manage.py train_recommender      # ~30 s, writes data/taste_model.json.gz
```

Run it again whenever titles are added to the catalogue, or the new ones
cannot be recommended. The corpus is gitignored; the model is committed.

`train_recommender` fails if any answer in `questions.py` names a term the
trained vocabulary does not contain, so a renamed tag cannot quietly turn an
answer into a no-op. It also prints the honest numbers, all on held-out data:

- **recall@10 on held-out edges** — hide a tenth of the recommendation graph,
  then ask whether those pairings come back as near neighbours.
- **description → neighbours recall@10** — the end-to-end one. Describe a
  title the model never trained on and see whether it returns the titles that
  title's fans actually watch. This is what the questionnaire does, and it is
  the number to tune against: the projection's cosine and R² peak at a
  *lower* dimension than the ranking does, so optimising them picks the worse
  model.

---

## Tests

```bash
python manage.py test --settings=config.settings.test
python smoke_check.py     # route, redirect and access-boundary check
```

The recommender's training tests skip unless numpy is installed — they cover
a build step, not the request path. Its scoring tests always run, against a
hand-built four-axis model rather than the real artifact, so an assertion
about ranking is an assertion about the rules and not about what AniList
voters happened to think this month.

The suite covers the service layer (cart limits, order settlement, payment
verification, event capacity, ratings), the selectors (including query counts,
so an N+1 regression fails the build), and the access boundaries that matter:
that one user cannot read another user's order, that state changes reject GET,
and that private pages redirect anonymous visitors.

---
