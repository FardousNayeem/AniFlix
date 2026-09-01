"""Migrate data out of the previous single-app database.

The old schema kept everything in one ``base`` app and modelled a cart and a
placed order as the same row. Rather than performing migration surgery on that
shape, this command reads the legacy SQLite file directly and writes the new
models, mapping the differences explicitly:

  base_user          -> accounts.User          (username dropped as identity)
  base_genre         -> streaming.Genre        (slugs generated)
  base_anime         -> streaming.Anime        (release_date -> release_year)
  base_episode       -> streaming.Episode      (unnumbered episodes get an index)
  base_animerating   -> streaming.Rating       (duplicates collapse to the latest)
  base_anime_favorites -> streaming.WatchlistEntry
  base_comment       -> streaming.Comment
  base_location      -> events.Venue
  base_event         -> events.Event           (date -> starts_at at 18:00 local)
  base_contributor   -> events.Registration    (duplicates collapse to one seat)
  base_product       -> shop.Product           (related_anime text -> FK or collection)
  base_order (completed=1) -> shop.Order       (prices snapshotted at import)
  base_order (completed=0) -> shop.Cart

Idempotent: run it twice and nothing is duplicated.

    python manage.py import_legacy /path/to/old/db.sqlite3 \
        --media-source /path/to/old/static/images
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import User
from apps.events.models import Event, Registration, Venue
from apps.shop.models import Cart, CartItem, Order, OrderItem, Product
from apps.streaming.models import Anime, Comment, Episode, Genre, Rating, WatchlistEntry


class Command(BaseCommand):
    help = "Import data from the previous ANIFLIX database into the new schema."

    def add_arguments(self, parser):
        parser.add_argument("legacy_db", type=str, help="Path to the old db.sqlite3.")
        parser.add_argument(
            "--media-source",
            type=str,
            default="",
            help="Old media directory to copy image files from (usually static/images).",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change and roll back."
        )

    def handle(self, *args, **options):
        legacy_path = Path(options["legacy_db"]).expanduser().resolve()
        if not legacy_path.exists():
            raise CommandError(f"Legacy database not found: {legacy_path}")

        self.media_source = Path(options["media_source"]).expanduser() if options["media_source"] else None
        self.counts: dict[str, int] = {}

        connection = sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row

        try:
            with transaction.atomic():
                self._import(connection)
                if options["dry_run"]:
                    self.stdout.write(self.style.WARNING("Dry run: rolling back."))
                    transaction.set_rollback(True)
        finally:
            connection.close()

        for label, count in self.counts.items():
            self.stdout.write(self.style.SUCCESS(f"{label:<24} {count}"))
        self.stdout.write(self.style.SUCCESS("Import finished."))

    # -- helpers -----------------------------------------------------------
    def _tally(self, label: str, amount: int = 1) -> None:
        self.counts[label] = self.counts.get(label, 0) + amount

    def _tables(self, connection) -> set[str]:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row["name"] for row in rows}

    def _rows(self, connection, table: str) -> list[sqlite3.Row]:
        if table not in self._table_names:
            return []
        return connection.execute(f'SELECT * FROM "{table}"').fetchall()

    def _copy_media(self, relative_name: str | None, destination_dir: str) -> str:
        """Copy an old image into the new MEDIA_ROOT and return its stored name."""
        if not relative_name or not self.media_source:
            return ""
        source = self.media_source / relative_name
        if not source.exists():
            # Old rows sometimes stored a bare filename with no directory.
            source = self.media_source / Path(relative_name).name
        if not source.exists():
            return ""

        target_name = f"{destination_dir}/{Path(relative_name).name}"
        target = Path(settings.MEDIA_ROOT) / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        return target_name

    # -- import ------------------------------------------------------------
    def _import(self, connection) -> None:
        self._table_names = self._tables(connection)

        users = self._import_users(connection)
        genres = self._import_genres(connection)
        animes = self._import_animes(connection, genres)
        self._import_episodes(connection, animes)
        self._import_ratings(connection, users, animes)
        self._import_watchlist(connection, users, animes)
        self._import_comments(connection, users)
        venues = self._import_venues(connection)
        events = self._import_events(connection, venues)
        self._import_registrations(connection, users, events)
        products = self._import_products(connection, animes)
        self._import_orders(connection, users, products)

    def _import_users(self, connection) -> dict[int, User]:
        mapping: dict[int, User] = {}
        for row in self._rows(connection, "base_user"):
            email = (row["email"] or "").strip().lower()
            if not email:
                # The old model allowed a null email while authenticating on it.
                email = f"legacy-{row['id']}@aniflix.invalid"

            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "username": email,
                    "password": row["password"] or "",
                    "display_name": (row["name"] or "").strip(),
                    "bio": (row["bio"] or "").strip(),
                    "phone": (row["phone"] or "").strip()[:32],
                    "address": (row["address"] or "").strip()[:255],
                    "gender": self._map_gender(row["gender"]),
                    "newsletter_opt_in": bool(row["newslatter"]),
                    "is_staff": bool(row["is_staff"]),
                    "is_superuser": bool(row["is_superuser"]),
                    "is_active": bool(row["is_active"]),
                    "date_joined": self._parse_datetime(row["date_joined"]) or timezone.now(),
                },
            )
            if created:
                avatar = self._copy_media(row["avatar"], "avatars")
                if avatar:
                    user.avatar.name = avatar
                    user.save(update_fields=["avatar"])
                self._tally("users imported")
            mapping[row["id"]] = user
        return mapping

    @staticmethod
    def _map_gender(raw: str | None) -> str:
        value = (raw or "").strip().lower()
        return {"male": "male", "female": "female", "non-binary": "non_binary"}.get(value, "")

    @staticmethod
    def _parse_datetime(raw):
        if not raw:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(str(raw)[:26], fmt)
            except ValueError:
                continue
            return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
        return None

    @staticmethod
    def _parse_date(raw):
        parsed = Command._parse_datetime(raw)
        return parsed.date() if parsed else None

    def _import_genres(self, connection) -> dict[int, Genre]:
        mapping: dict[int, Genre] = {}
        for row in self._rows(connection, "base_genre"):
            genre, created = Genre.objects.get_or_create(
                name=row["name"], defaults={"slug": slugify(row["name"])[:200]}
            )
            if created:
                self._tally("genres imported")
            mapping[row["id"]] = genre
        return mapping

    def _import_animes(self, connection, genres) -> dict[int, Anime]:
        mapping: dict[int, Anime] = {}
        genre_links: dict[int, list[int]] = {}
        for row in self._rows(connection, "base_anime_genres"):
            genre_links.setdefault(row["anime_id"], []).append(row["genre_id"])

        for row in self._rows(connection, "base_anime"):
            anime = Anime.objects.filter(name=row["name"]).first()
            if anime is None:
                anime = Anime(
                    name=row["name"],
                    studio=(row["studio"] or "").strip(),
                    release_year=self._safe_year(row["release_date"]),
                    synopsis=(row["description"] or "").strip(),
                    view_count=row["views"] or 0,
                )
                poster = self._copy_media(row["image"], "anime/posters")
                if poster:
                    anime.poster.name = poster
                anime.save()
                self._tally("anime imported")

            linked = [genres[gid] for gid in genre_links.get(row["id"], []) if gid in genres]
            if linked:
                anime.genres.set(linked)
            mapping[row["id"]] = anime

        # Nothing is pinned on import: the spotlight ranks by recent bookmarks
        # on its own, and pinning is an editorial decision made in the admin.
        return mapping

    @staticmethod
    def _safe_year(raw) -> int:
        try:
            year = int(raw)
        except (TypeError, ValueError):
            return 2000
        return year if 1900 <= year <= 2100 else 2000

    def _import_episodes(self, connection, animes) -> None:
        # The old schema allowed a null episode number and had no uniqueness
        # constraint, so numbers are backfilled per anime in insertion order.
        per_anime: dict[int, int] = {}
        for row in self._rows(connection, "base_episode"):
            anime = animes.get(row["anime_id"])
            if anime is None:
                continue

            number = row["number"]
            if not number:
                per_anime[row["anime_id"]] = per_anime.get(row["anime_id"], 0) + 1
                number = per_anime[row["anime_id"]]
            else:
                per_anime[row["anime_id"]] = max(per_anime.get(row["anime_id"], 0), int(number))

            _episode, created = Episode.objects.get_or_create(
                anime=anime,
                number=int(number),
                defaults={
                    "title": (row["name"] or f"Episode {number}").strip(),
                    "air_date": self._parse_date(row["release_date"]),
                    "video_url": (row["video_link"] or "").strip(),
                },
            )
            if created:
                self._tally("episodes imported")

    def _import_ratings(self, connection, users, animes) -> None:
        for row in self._rows(connection, "base_animerating"):
            user = users.get(row["user_id"])
            anime = animes.get(row["anime_id"])
            score = row["rating"]
            if not user or not anime or not score:
                continue
            score = max(1, min(5, int(score)))
            # update_or_create collapses the duplicate rows the old code created.
            _rating, created = Rating.objects.update_or_create(
                user=user, anime=anime, defaults={"score": score}
            )
            if created:
                self._tally("ratings imported")

    def _import_watchlist(self, connection, users, animes) -> None:
        for row in self._rows(connection, "base_anime_favorites"):
            user = users.get(row["user_id"])
            anime = animes.get(row["anime_id"])
            if not user or not anime:
                continue
            _entry, created = WatchlistEntry.objects.get_or_create(user=user, anime=anime)
            if created:
                self._tally("watchlist entries")

    def _import_comments(self, connection, users) -> None:
        episodes_by_legacy_id: dict[int, Episode] = {}
        legacy_episodes = {row["id"]: row for row in self._rows(connection, "base_episode")}
        legacy_animes = {row["id"]: row["name"] for row in self._rows(connection, "base_anime")}

        for legacy_id, row in legacy_episodes.items():
            anime_name = legacy_animes.get(row["anime_id"])
            if not anime_name:
                continue
            episode = Episode.objects.filter(
                anime__name=anime_name, title=(row["name"] or "").strip()
            ).first()
            if episode:
                episodes_by_legacy_id[legacy_id] = episode

        for row in self._rows(connection, "base_comment"):
            user = users.get(row["user_id"])
            episode = episodes_by_legacy_id.get(row["episode_id"])
            body = (row["text"] or "").strip()
            if not user or not episode or not body:
                continue
            if Comment.objects.filter(user=user, episode=episode, body=body).exists():
                continue
            Comment.objects.create(user=user, episode=episode, body=body)
            self._tally("comments imported")

    def _import_venues(self, connection) -> dict[int, Venue]:
        mapping: dict[int, Venue] = {}
        for row in self._rows(connection, "base_location"):
            venue, created = Venue.objects.get_or_create(
                name=row["name"], defaults={"address": row["address"] or ""}
            )
            if created:
                self._tally("venues imported")
            mapping[row["id"]] = venue
        return mapping

    def _import_events(self, connection, venues) -> dict[int, Event]:
        mapping: dict[int, Event] = {}
        fallback_venue = None

        for row in self._rows(connection, "base_event"):
            venue = venues.get(row["location_id"])
            if venue is None:
                if fallback_venue is None:
                    fallback_venue, _ = Venue.objects.get_or_create(
                        name="To be announced", defaults={"address": "Venue to be confirmed"}
                    )
                venue = fallback_venue

            slug = (row["slug"] or slugify(row["title"]))[:200]
            event = Event.objects.filter(slug=slug).first()
            if event is None:
                event_date = self._parse_date(row["date"]) or timezone.localdate()
                starts_at = timezone.make_aware(datetime.combine(event_date, time(18, 0)))
                event = Event(
                    title=row["title"][:200],
                    slug=slug,
                    description=(row["description"] or "").strip(),
                    organiser_email=row["supervisor_email"] or "events@aniflix.test",
                    host_name=(row["group_name"] or "").strip()[:200],
                    starts_at=starts_at,
                    venue=venue,
                )
                cover = self._copy_media(row["image"], "events")
                if cover:
                    event.cover.name = cover
                event.save()
                self._tally("events imported")
            mapping[row["id"]] = event
        return mapping

    def _import_registrations(self, connection, users, events) -> None:
        for row in self._rows(connection, "base_contributor"):
            user = users.get(row["user_id"])
            event = events.get(row["event_id"])
            if not user or not event:
                continue
            # get_or_create collapses the duplicate sign-ups the GET-based
            # registration handler produced.
            _registration, created = Registration.objects.get_or_create(
                user=user, event=event, defaults={"contact_email": user.email, "contact_phone": user.phone}
            )
            if created:
                self._tally("registrations imported")

    def _import_products(self, connection, animes) -> dict[int, Product]:
        by_name = {anime.name.lower(): anime for anime in animes.values()}
        mapping: dict[int, Product] = {}

        for row in self._rows(connection, "base_product"):
            product = Product.objects.filter(name=row["name"]).first()
            if product is None:
                collection = (row["related_anime"] or "").strip()
                # The legacy column was free text naming a franchise. Where that
                # franchise is a title we stream it becomes a real FK; otherwise
                # it is kept as a collection label instead of being discarded.
                related = by_name.get(collection.lower())
                if collection and related is None:
                    self._tally("merch collections kept")
                product = Product(
                    name=row["name"][:200],
                    description=(row["description"] or "").strip()[:1000],
                    price=Decimal(str(row["price"] or 0)).quantize(Decimal("0.01")),
                    size=(row["size"] or "").strip()[:8],
                    # The old model had a nullable ``instock`` boolean and no
                    # quantity; anything marked in stock starts at 25 units.
                    stock=25 if row["instock"] else 0,
                    is_active=True,
                    collection=collection[:120],
                    related_anime=related,
                )
                image = self._copy_media(row["image"], "products")
                if image:
                    product.image.name = image
                product.save()
                self._tally("products imported")
            mapping[row["id"]] = product
        return mapping

    def _import_orders(self, connection, users, products) -> None:
        customers = {row["id"]: row["user_id"] for row in self._rows(connection, "base_customer")}
        items_by_order: dict[int, list[sqlite3.Row]] = {}
        for row in self._rows(connection, "base_orderitem"):
            if row["order_id"]:
                items_by_order.setdefault(row["order_id"], []).append(row)

        shipping_by_order = {
            row["order_id"]: row for row in self._rows(connection, "base_shippingaddress") if row["order_id"]
        }

        for row in self._rows(connection, "base_order"):
            user = users.get(customers.get(row["customer_id"]))
            if user is None:
                continue
            lines = items_by_order.get(row["id"], [])
            if not lines:
                continue

            if not row["completed"]:
                self._import_open_cart(user, lines, products)
                continue

            reference = f"OF-LEG{row['id']:06d}"
            if Order.objects.filter(reference=reference).exists():
                continue

            shipping = shipping_by_order.get(row["id"])
            order = Order(
                reference=reference,
                user=user,
                status=Order.Status.PAID,
                ship_to_name=user.public_name[:200],
                ship_to_phone=(user.phone or "n/a")[:32],
                ship_to_address=((shipping["address"] if shipping else user.address) or "n/a")[:255],
                ship_to_city=((shipping["city"] if shipping else user.city) or "n/a")[:120],
                ship_to_postcode=((shipping["zipcode"] if shipping else "") or "")[:20],
                transaction_id=(row["transaction_id"] or "")[:120],
                paid_at=self._parse_datetime(row["date_ordered"]) or timezone.now(),
            )
            order.save()

            total = Decimal("0.00")
            for line in lines:
                product = products.get(line["product_id"])
                if product is None:
                    continue
                quantity = max(1, int(line["quantity"] or 1))
                unit_price = product.price
                total += unit_price * quantity
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product.name,
                    product_image=product.image.name if product.image else "",
                    unit_price=unit_price,
                    quantity=quantity,
                )

            order.total = total
            # created_at is auto_now_add, so the original date is restored here.
            Order.objects.filter(pk=order.pk).update(
                total=total, created_at=order.paid_at
            )
            self._tally("orders imported")

    def _import_open_cart(self, user, lines, products) -> None:
        cart, _created = Cart.objects.get_or_create(user=user)
        for line in lines:
            product = products.get(line["product_id"])
            if product is None:
                continue
            quantity = max(1, int(line["quantity"] or 1))
            _item, created = CartItem.objects.get_or_create(
                cart=cart, product=product, defaults={"quantity": quantity}
            )
            if created:
                self._tally("cart items imported")
