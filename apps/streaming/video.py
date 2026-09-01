"""Resolve a stored video link into something a browser can actually play.

The previous build never embedded anything. It drew a grey box with a CSS
triangle on it and wired that to ``target="_blank"``, so "watching an episode"
meant leaving the site. The links themselves were fine; nothing had tried to
play them.

Three things have to be right for an embed to work, and this module handles
all three:

1. **Scheme.** Half the stored links are ``http://`` or protocol-relative.
   Served over HTTPS the browser blocks them as mixed content, silently.
2. **Form.** A YouTube *watch* or *share* link cannot be framed; only the
   ``/embed/<id>`` form can. Same for Vimeo and Dailymotion.
3. **Kind.** A link to an ``.mp4`` is not an embed at all. It belongs in a
   native ``<video>`` element, which gives real in-page controls.

Anything unrecognised is still framed as-is, because most anime hosts serve a
purpose-built ``/embed/`` page. If a host does refuse to be framed, the player
detects that at runtime and offers the link instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

FILE_EXTENSIONS = {".mp4", ".webm", ".ogv", ".ogg", ".mov", ".m4v", ".m3u8"}

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
                 "www.youtube-nocookie.com", "youtube-nocookie.com"}
VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}
DAILYMOTION_HOSTS = {"dailymotion.com", "www.dailymotion.com", "dai.ly"}

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# A host is only plausible if it has a dot and no spaces. Guards against
# free-text that found its way into the column before URLField was enforced.
PLAUSIBLE_HOST = re.compile(r"^[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?::\d+)?$")


@dataclass(frozen=True)
class VideoSource:
    """What the template needs to render a player."""

    kind: str          # "file" | "embed" | "none"
    url: str = ""      # what goes in <video src> or <iframe src>
    link_url: str = "" # where "open in a new tab" points
    provider: str = "" # human-readable, used in the fallback copy
    mime: str = ""     # only for kind == "file"

    @property
    def is_playable(self) -> bool:
        return self.kind != "none"

    @property
    def is_file(self) -> bool:
        return self.kind == "file"

    @property
    def is_embed(self) -> bool:
        return self.kind == "embed"


NO_SOURCE = VideoSource(kind="none")

_MIME_BY_EXTENSION = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".ogg": "video/ogg",
    ".m3u8": "application/vnd.apple.mpegurl",
}


def normalise_scheme(raw: str) -> str:
    """Force https. Mixed content is blocked without any visible error."""
    url = (raw or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    if not url.startswith("https://"):
        return f"https://{url}"
    return url


def resolve(raw: str) -> VideoSource:
    """Turn a stored link into a playable source."""
    url = normalise_scheme(raw)
    if not url:
        return NO_SOURCE

    try:
        parsed = urlparse(url)
    except ValueError:
        return NO_SOURCE
    if not parsed.netloc:
        return NO_SOURCE

    host = parsed.netloc.lower()
    if not PLAUSIBLE_HOST.match(host):
        return NO_SOURCE
    path = parsed.path or ""

    extension = _extension(path)
    if extension in FILE_EXTENSIONS:
        return VideoSource(
            kind="file", url=url, link_url=url, provider="Direct file",
            mime=_MIME_BY_EXTENSION.get(extension, ""),
        )

    if host in YOUTUBE_HOSTS:
        return _youtube(parsed, url)
    if host in VIMEO_HOSTS:
        return _vimeo(parsed, url)
    if host in DAILYMOTION_HOSTS:
        return _dailymotion(parsed, url)

    # Unknown host. Most anime mirrors publish a dedicated /embed/ page, so
    # framing it as-is is the right default.
    return VideoSource(kind="embed", url=url, link_url=url, provider=_pretty_host(host))


def _extension(path: str) -> str:
    _, _, tail = path.rpartition("/")
    if "." not in tail:
        return ""
    return "." + tail.rsplit(".", 1)[-1].lower()


def _pretty_host(host: str) -> str:
    return host.removeprefix("www.")


def _start_seconds(query: dict[str, list[str]]) -> int:
    """Read a start offset from ``t`` or ``start``, accepting ``90`` or ``1m30s``."""
    for key in ("start", "t"):
        values = query.get(key)
        if not values:
            continue
        raw = values[0].strip().lower()
        if raw.isdigit():
            return int(raw)
        match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
        if match and any(match.groups()):
            hours, minutes, seconds = (int(part or 0) for part in match.groups())
            return hours * 3600 + minutes * 60 + seconds
    return 0


def _youtube(parsed, url: str) -> VideoSource:
    """Accept every YouTube form and emit the one that can be framed."""
    host = parsed.netloc.lower()
    path = (parsed.path or "").strip("/")
    query = parse_qs(parsed.query)

    video_id = ""
    if host in {"youtu.be"}:
        video_id = path.split("/", 1)[0]
    elif path.startswith("embed/"):
        video_id = path[len("embed/") :].split("/", 1)[0]
    elif path.startswith("shorts/"):
        video_id = path[len("shorts/") :].split("/", 1)[0]
    elif path.startswith("live/"):
        video_id = path[len("live/") :].split("/", 1)[0]
    elif query.get("v"):
        video_id = query["v"][0]

    if not YOUTUBE_ID.match(video_id or ""):
        # Not a single video (a playlist or channel URL). Do not guess.
        return VideoSource(kind="embed", url=url, link_url=url, provider="YouTube")

    # youtube-nocookie serves the same player without setting tracking cookies
    # on first paint. rel=0 keeps recommendations to the same channel.
    params = "rel=0&modestbranding=1&playsinline=1"
    start = _start_seconds(query)
    if start:
        params += f"&start={start}"

    return VideoSource(
        kind="embed",
        url=f"https://www.youtube-nocookie.com/embed/{video_id}?{params}",
        link_url=f"https://www.youtube.com/watch?v={video_id}" + (f"&t={start}" if start else ""),
        provider="YouTube",
    )


def _vimeo(parsed, url: str) -> VideoSource:
    path = (parsed.path or "").strip("/")
    if path.startswith("video/"):
        path = path[len("video/") :]
    video_id = path.split("/", 1)[0]
    if not video_id.isdigit():
        return VideoSource(kind="embed", url=url, link_url=url, provider="Vimeo")
    return VideoSource(
        kind="embed",
        url=f"https://player.vimeo.com/video/{video_id}",
        link_url=f"https://vimeo.com/{video_id}",
        provider="Vimeo",
    )


def _dailymotion(parsed, url: str) -> VideoSource:
    path = (parsed.path or "").strip("/")
    host = parsed.netloc.lower()
    if host == "dai.ly":
        video_id = path.split("/", 1)[0]
    elif path.startswith("embed/video/"):
        video_id = path[len("embed/video/") :].split("/", 1)[0]
    elif path.startswith("video/"):
        video_id = path[len("video/") :].split("/", 1)[0]
    else:
        video_id = ""

    if not video_id:
        return VideoSource(kind="embed", url=url, link_url=url, provider="Dailymotion")
    return VideoSource(
        kind="embed",
        url=f"https://www.dailymotion.com/embed/video/{video_id}",
        link_url=f"https://www.dailymotion.com/video/{video_id}",
        provider="Dailymotion",
    )
