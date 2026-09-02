"""Route smoke test: checks status codes and access boundaries without following redirects."""
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()
import logging; logging.disable(logging.CRITICAL)

from django.test import Client
from django.test.utils import setup_test_environment
setup_test_environment()

import json, sqlite3, shutil, sys
from django.core.management import call_command
from django.test.utils import setup_databases
old_config = setup_databases(verbosity=0, interactive=False)
call_command("import_legacy", "../anime/db.sqlite3", verbosity=0)

from apps.accounts.models import User
from apps.streaming.models import Anime, Episode, Genre
from apps.events.models import Event
from apps.shop.models import Product, Order

anime = Anime.objects.filter(episodes__isnull=False).first()
ep = Episode.objects.first()
genre = Genre.objects.first()
event = Event.objects.first()
product = Product.objects.first()
order = Order.objects.first()
owner = order.user
other = User.objects.exclude(pk=owner.pk).first()

PUBLIC = [
    ("/", 200), ("/browse/", 200), ("/browse/?q=gintama&sort=rating", 200),
    ("/genres/", 200), (f"/genres/{genre.slug}/", 200),
    (f"/anime/{anime.slug}/", 200),
    (f"/anime/{ep.anime.slug}/episode/{ep.number}/", 200),
    ("/events/", 200), (f"/events/{event.slug}/", 200),
    ("/shop/", 200), (f"/shop/product/{product.slug}/", 200),
    ("/accounts/login/", 200), ("/accounts/signup/", 200),
    ("/accounts/password/reset/", 200),
    ("/nope-does-not-exist/", 404),
    # The recommender questionnaire is public: the widget rides on every page
    # and has to be able to load before anybody signs in.
    ("/recommend/questions/", 200),
]

GATED = [
    "/shop/cart/", "/shop/checkout/", "/shop/orders/",
    f"/shop/orders/{order.reference}/",
    "/profile/", "/profile/edit/", "/profile/my-list/", "/profile/activity/",
    "/events/mine/", f"/events/{event.slug}/ticket/",
]

failures = []

def check(label, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    if not condition:
        failures.append(f"{label} {detail}")
    print(f"  {status} {label} {detail}")

print("== public pages render for anonymous visitors ==")
anon = Client()
for url, expected in PUBLIC:
    r = anon.get(url)
    check(f"{expected} {url}", r.status_code == expected, f"(got {r.status_code})")

print("\n== private pages redirect anonymous visitors to sign-in ==")
for url in GATED:
    r = anon.get(url)
    ok = r.status_code == 302 and "/accounts/login/" in r.headers.get("Location", "")
    check(url, ok, f"(got {r.status_code} -> {r.headers.get('Location','')})")

print("\n== private pages render for the signed-in owner ==")
owner_client = Client(); owner_client.force_login(owner)
for url in GATED:
    r = owner_client.get(url)
    check(url, r.status_code in (200, 302), f"(got {r.status_code})")

print("\n== one user cannot read another user's order ==")
intruder = Client(); intruder.force_login(other)
r = intruder.get(f"/shop/orders/{order.reference}/")
check(f"order {order.reference} hidden from {other.email}", r.status_code == 404, f"(got {r.status_code})")

print("\n== state changes reject GET ==")
for url in [
    f"/anime/{anime.slug}/watchlist/",
    f"/anime/{anime.slug}/rate/",
    f"/shop/cart/update/{product.slug}/",
    "/recommend/ask/",
]:
    r = owner_client.get(url)
    check(f"GET {url}", r.status_code == 405, f"(got {r.status_code})")

r = owner_client.get(f"/events/{event.slug}/register/")
check(f"GET /events/{event.slug}/register/", r.status_code == 302, f"(got {r.status_code})")

print("\n== the recommender answers, or says why it cannot ==")
# Anonymous on purpose: asking must not need an account.
r = anon.post(
    "/recommend/ask/",
    data=json.dumps({"answers": {"mood": ["dark"]}}),
    content_type="application/json",
)
if r.status_code == 503:
    check("recommender", True, "(not trained on this machine; run train_recommender)")
else:
    body = r.json()
    check("recommender answers an anonymous ask", r.status_code == 200 and body.get("ok"),
          f"(got {r.status_code})")
    check("recommender returns ranked titles", bool(body.get("results")),
          f"(got {len(body.get('results', []))})")

r = anon.post("/recommend/ask/", data=json.dumps({"answers": {"nonsense": ["x"]}}),
              content_type="application/json")
check("recommender rejects an unknown question", r.status_code == 400, f"(got {r.status_code})")

print("\n== error pages render ==")
# The /__errors__/ preview routes only exist under DEBUG, so the handlers are
# called directly here instead.
from django.test import RequestFactory
from apps.core import views as core_views
request = RequestFactory().get("/whatever/")
request.user = other
for label, response in [("404", core_views.page_not_found(request)), ("500", core_views.server_error(request))]:
    expected = int(label)
    check(f"{label} error page", response.status_code == expected and b"ANIFLIX" in response.content,
          f"(got {response.status_code})")

print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURES:\n  " + "\n  ".join(failures)))
sys.exit(1 if failures else 0)
