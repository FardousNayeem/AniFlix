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

python manage.py migrate
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

### Bringing the old data across

The previous build kept everything in one `base` app and used one table for
both carts and placed orders. Rather than perform migration surgery on that
shape, there is an importer that reads the old database directly:

```bash
python manage.py import_legacy /path/to/old/db.sqlite3 \
    --media-source /path/to/old/anime/static/images
```

It is idempotent (safe to re-run) and supports `--dry-run`. What it maps, and
which duplicates it collapses, is documented at the top of
`apps/core/management/commands/import_legacy.py`.

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
templates/              project templates; app folders mirror the app names
static/css/             tokens -> base -> components -> layout -> pages
static/js/app.js        all front-end behaviour, no framework
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

## Tests

```bash
python manage.py test --settings=config.settings.test
python smoke_check.py     # route, redirect and access-boundary check
```

The suite covers the service layer (cart limits, order settlement, payment
verification, event capacity, ratings), the selectors (including query counts,
so an N+1 regression fails the build), and the access boundaries that matter:
that one user cannot read another user's order, that state changes reject GET,
and that private pages redirect anonymous visitors.

---

## Front end

No jQuery, no Bootstrap, no build step. `static/css/tokens.css` holds the whole
palette, radius scale and type scale; every other stylesheet reads from it.

The theme is committed dark, deliberately: this is a product people watch video
in, and the brand was already dark navy. There is no light variant to drift out
of sync.

`static/js/app.js` is one file of progressive enhancement. Every page works with
scripting disabled, every animation honours `prefers-reduced-motion`, and there
is no `scroll` event listener anywhere (IntersectionObserver instead).

### What is interactive

| Behaviour                | Where                    | Falls back to                     |
| ------------------------ | ------------------------ | --------------------------------- |
| Instant search + `/` key | catalogue pages          | a plain GET search form           |
| Add to cart              | shop, product page       | a normal POST and redirect        |
| Cart quantity and totals | cart page, patched live  | a normal POST and redirect        |
| Rating                   | anime page, saves on pick| a submit button                   |
| My List toggle           | posters, anime page      | a normal POST and redirect        |
| Spotlight rotation       | home                     | the first title, statically       |
| Rail arrows              | trending, related        | native horizontal scrolling       |
| Episode arrow keys       | player                   | the previous/next buttons         |
| Copy reference           | receipts, tickets        | selecting the text                |
| Back to top              | every page               | scrolling                         |

Two rules the front end sticks to. Nothing is JavaScript-only: every control
above is a real form or link first. And nothing lies about state: the player,
for instance, never hides a video frame on a timeout, because a slow frame and
a blocked frame are indistinguishable from script.

### A DOM trap worth knowing about

A form control named `action` becomes a property of the form element and
shadows `HTMLFormElement.action`, so `form.action` returns the input instead of
the URL. The cart's operation field is called `op` for that reason, the
JavaScript reads `form.getAttribute("action")`, and a test asserts no cart field
uses a shadowing name.
