# What changed, and why

A record of the defects the rebuild fixed. Kept because most of them are not
visible from the new code alone, and because a few are worth not reintroducing.

---

## Security

**Anyone could read anyone's order.** `recipt(request, pk)` looked an order up
by primary key with no ownership check and no login requirement. Walking
`/shop/recipt/1`, `/2`, `/3` returned other customers' names, phone numbers,
addresses and order contents. Orders are now fetched through
`selectors.order_for_user(user=..., reference=...)`, which filters by the
requesting user before the lookup, so a mismatch is a 404.

**Anyone could read anyone's profile.** `/profile/<pk>` rendered any account's
phone number and address. There is one profile route now and it is self-only.

**A browser could mark an order paid.** `payment_confirmed` accepted a POST and,
if the body said `status=VALID`, set `completed=True`. It never asked SSLCommerz
whether the payment happened. Callbacks are now verified server-side against the
gateway's validation API, and the settled amount is compared to the order total,
so paying 1 BDT for a 5,000 BDT order is rejected.

**Live payment credentials were in the source.** Store id and password were
literals inside the view, alongside hardcoded `127.0.0.1` callback URLs and a
placeholder customer (`'jilan'`, `'abc@gmail.com'`, phone `'0123'`) sent to the
gateway on every real transaction. All of it comes from the environment now, and
the customer details are the customer's.

**`google-auth-key.json` and `payment sand.txt` were committed**, the latter
containing a card number and expiry. Both are covered by `.gitignore` and are
not carried into the new tree. Rotate the OAuth client secret: anything that has
been in a repository should be treated as public.

`DEBUG = True`, an empty `ALLOWED_HOSTS` and a hardcoded `SECRET_KEY` shipped in
the only settings file. Settings are split into base/dev/prod/test, and
production sets HSTS, secure cookies, `SECURE_SSL_REDIRECT` and
`X_FRAME_OPTIONS = DENY`.

---

## Correctness

**Placed orders rewrote themselves.** `Order.get_cart_total` summed
`product.price * quantity` from live product rows, and the same `Order` model
represented both an open cart and a completed purchase. Editing a product's
price in the admin silently changed what historical receipts said the customer
had paid, and `OrderItem.get_total` raised `AttributeError` on any order whose
product had since been deleted (`on_delete=SET_NULL`). `Cart`/`CartItem` and
`Order`/`OrderItem` are now separate models, and an order snapshots the name,
image and unit price at the moment of purchase.

**Ratings drifted.** `anime_detail` created a new `AnimeRating` row on every
submission with no uniqueness constraint, so one user could rate a title fifty
times and move its average. There is a `UniqueConstraint` on `(user, anime)` and
a `CheckConstraint` on the 1-5 range; rating again updates the existing row.

**A page load could register you for an event.** `registration_complete` was a
GET handler that created a `Contributor` row, with no uniqueness constraint. Any
crawler, prefetch or refresh signed the user up again; the imported data
contained duplicates. Registration is POST-only, unique per `(user, event)`, and
checks capacity under a row lock so the last seat cannot be sold twice.

**Guest pages returned 500.** `cartData()` assigned `cartItems`, `order` and
`items` only inside `if request.user.is_authenticated`, then returned them
unconditionally, raising `UnboundLocalError` for anonymous visitors. `cart`,
`checkout`, `updateItem`, `order_history` and `recipt` had no `login_required`,
so every one of them was a 500 for a logged-out user.

**A late failure callback could unmark a paid order.** Found by a test written
during the rebuild: `mark_order_failed` checked the status on the in-memory
instance rather than re-reading it, so a failure notification arriving after a
successful payment flipped a paid order to failed. The status is now re-read
under `select_for_update`. `cancel_order` had the same shape and the same fix.

**The cart reported the wrong limit.** Also found by a test: asking for 99 of an
item with 5 in stock reported the per-item cap rather than the stock, because
the cap was checked first. The tighter limit is reported now.

**Dead and broken code.** `context_processors.py` imported `Order_Product`, a
model that does not exist; it would have raised on every request had it ever
been added to `TEMPLATES`. `processOrder` passed `customer=` to a field named
`Customer`. `checkout.js` referenced undefined globals and was never loaded.
`anime_list` had no URL. None of it survives.

**Two views rendered a template that does not exist.** `loginPage` and
`registerPage` both rendered `base/login-register.html`, which is absent from
the repository, so `/login/` and `/register/` were `TemplateDoesNotExist`. There
is one authentication path now, allauth's.

---

## The video player

The original player was a grey `div` with a CSS triangle drawn on it, wired to
`target="_blank"`. Clicking an episode left the site. The workaround existed
because embedding appeared not to work.

Embedding works. Checking the hosts in the catalogue, neither `animegg.org` nor
`youtube.com` sends `X-Frame-Options` or a `frame-ancestors` policy; both frame
fine. What was actually wrong with the stored links was:

- **Scheme.** Many are `http://` or protocol-relative `//host/...`. Served over
  HTTPS, the browser blocks those as mixed content with no visible error.
- **Form.** A YouTube *watch* or *share* URL cannot be framed at all. Only
  `/embed/<id>` can.
- **Kind.** A link to an `.mp4` is not an embed; it belongs in a `<video>`
  element.

`apps/streaming/video.py` resolves all three. It forces HTTPS, converts every
YouTube form (`watch?v=`, `youtu.be`, `shorts/`, `live/`) to a
`youtube-nocookie` embed while preserving any start offset, does the same for
Vimeo and Dailymotion, routes direct media files to a native `<video>` element
with real controls, and frames anything else as-is. Episodes now play in the
page.

One detail worth keeping: a cross-origin frame that is refused does not fire an
`error` event, and a frame that is merely slow is indistinguishable from one
that is blocked. There is no signal that separates them, so the player never
hides a frame on a guess. If nothing has loaded within the grace period the
"open on <provider>" line below simply becomes prominent. A slow connection
loses nothing.

(Some of the seeded YouTube videos are Muse Asia uploads, which are
region-locked at the source. YouTube's own player says so, and the escape link
sits directly beneath it. That is data, not a defect.)

---

## Performance

`genre_list` ran one query per genre and then one per title in the template, and
rendered every genre's entire catalogue into hidden Bootstrap modals on a single
page, so opening `/genre/` downloaded the whole library. Each genre is a real,
paginated page now.

`Home` and `shop` fetched without `select_related`/`prefetch_related`; poster
cards then triggered a query per card for ratings and favourites. The catalogue
resolves ratings, episode counts, genres and per-user watchlist state in two
queries regardless of page size, and a test asserts the count so a regression
fails the build.

`anime_detail` incremented `views` with a read-modify-write; concurrent requests
lost counts. It is an atomic `F()` update.

`shop`'s "new arrivals" sort called `list(reversed(products))`, loading the
entire product table into memory to reverse it. It is `order_by`.

---

## Architecture

One `base` app held all three products: a 174-line `models.py`, a 496-line
`views.py` and a single flat `urls.py` covering streaming, events and commerce,
with wildcard imports throughout.

There are five apps now (`core`, `accounts`, `streaming`, `events`, `shop`),
each with its own URLconf and namespace, and each splitting reads
(`selectors.py`) from writes (`services.py`). Views validate input, call one
function and render. Payment providers sit behind a `PaymentGateway` protocol,
so the sandbox gateway and SSLCommerz are interchangeable and the checkout code
never imports a vendor SDK.

Naming was normalised on the way through: `recipt` to `order-detail`,
`newslatter` to `newsletter_opt_in`, `socialize`/`Contributor` to
`events`/`Registration`, `Location` to `Venue`, `favorites` to a `WatchlistEntry`
model that can carry a date, and camelCase view names to Django's convention.
The `Customer` model, a one-to-one wrapper around `User` that held a single
duplicated name field, is gone.

Legacy `Product.related_anime` was free text naming a franchise, and most values
(`Berserk`, `Cowboy Bebop`, `Dragon Ball Z`) name series the catalogue does not
stream. Rather than dropping that data, `Product` has both a `collection` label
and an optional real foreign key, and the importer decides which applies.

---

## Interface

The old front end loaded jQuery three times, Bootstrap 4 and Bootstrap 5
together, plus wow.js, waypoints, counterup and owlcarousel, to run one carousel
and one dropdown. Several templates opened a second `<html>` document inside a
`{% block %}`; others used `{% block content %}` with no `{% extends %}`, so the
block was silently discarded. Two different navbars existed on two different
Bootstrap versions, and pages were held together with inline styles.

There is one base template, one navbar, one footer, and a token-driven
stylesheet split into tokens, base, components, layout and pages. All behaviour
is one dependency-free file. Every list has an empty state, every destructive
action asks first, every form labels above the input and shows errors below it,
and messages surface as toasts rather than unstyled text.

Deliberate choices worth recording:

- **Dark only.** A streaming product is watched in a dark room and the brand was
  already dark navy. A light variant would be a second palette to keep in sync
  for no one.
- **The brand yellow stayed.** `#d6d012` carries over from the original build.
  It is the one accent on the page.
- **One radius system.** Surfaces 16px, controls 10px, chips and avatars full.
- **The product is called ANIFLIX.** The brand name is set once, in
  `SITE_NAME`, and the wordmark lives in two partials.
- **Motion is justified or absent.** The spotlight rotates because it promotes
  several titles; the scroll reveal sequences a long grid; the rail arrows exist
  because a rail cannot be scrolled with a plain mouse wheel. Everything
  collapses under `prefers-reduced-motion`, and no code listens to `scroll`.


---

## Interaction

The old front end had exactly two interactive behaviours: an add-to-cart fetch,
and a favourite toggle that cached its state in `localStorage`, so the button
could disagree with the database after a change made anywhere else. Everything
else was a full page load, including changing a cart quantity and submitting a
rating.

What is interactive now, and why each one earns its place:

- **Instant search**, on every catalogue page, with `/` to focus, arrow keys to
  move and Escape to dismiss. Two characters minimum, debounced, results capped
  server-side, and a small cache so retyping a query does not hit the network.
  It exists because a search that will return nothing should be obvious before
  submitting.
- **Cart updates patch the page.** The cart used to `location.reload()` on every
  quantity change, losing scroll position. The response now carries per-line
  totals and the subtotal, and the changed row flashes so the update is noticed.
- **Ratings save on pick.** Choosing a star used to reload the whole page in
  order to display a number that was already on screen.
- **Rail arrows**, because a horizontal rail cannot be scrolled with a plain
  mouse wheel, and the arrow only appears when the rail actually overflows.
- **Arrow keys move between episodes**, with a visible hint. This is a player.
- **Copy buttons** on the order and ticket references, the two strings people
  otherwise retype by hand.
- **Back to top**, driven by an IntersectionObserver sentinel rather than a
  scroll listener.

Everything above degrades: each control is a real form or link, and the pages
work with scripting off.

### One bug worth recording

A hidden field named `action` inside the add-to-cart form shadowed
`HTMLFormElement.action`, so `form.action` in JavaScript returned the input
element rather than the URL, and every add-to-cart quietly posted to
`/shop/[object HTMLInputElement]` and 404ed. Nothing in the Django test suite
could catch it, because the tests post to the URL directly; it only appeared
when the page was driven in a real browser. The field is now called `op`, the
JavaScript reads `getAttribute("action")`, and a test fails if any cart field
takes a name that shadows a form property.
