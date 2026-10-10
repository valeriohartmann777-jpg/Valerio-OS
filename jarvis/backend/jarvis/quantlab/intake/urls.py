"""Links to social videos: what may be fetched, from where, and what that yields.

JARVIS never fetches an arbitrary page. A link is parsed and matched against a
short allowlist of video platforms; only their official, public oEmbed
endpoints are called, and only for metadata (caption, author, thumbnail).
Video, audio and transcripts are not available that way — the honest result is
``METADATA_ONLY`` / ``EMBED_ONLY`` with ``REQUIRES_UPLOAD`` as the next step.

SSRF defence: https only, no credentials or custom ports in the URL, no IP
literals, the host must be on the allowlist, every resolved address must be a
public one, redirects are followed by hand (≤ 3) and each hop is re-checked,
and responses are size-capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qs, quote, urlsplit

import httpx2

from jarvis.quantlab.intake.detect import IntakeError

Provider = Literal["tiktok", "youtube", "instagram", "other"]
Capability = Literal[
    "METADATA_ONLY",
    "EMBED_ONLY",
    "TRANSCRIPT_AVAILABLE",
    "VIDEO_ACCESS_AUTHORIZED",
    "UNAVAILABLE",
    "REQUIRES_UPLOAD",
]
DOMAINS: dict[str, tuple[str, ...]] = {
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "instagram": ("instagram.com",),
}
OEMBED = {
    "tiktok": "https://www.tiktok.com/oembed?url={url}",
    "youtube": "https://www.youtube.com/oembed?url={url}&format=json",
}
MAX_RESPONSE = 256 * 1024
MAX_REDIRECTS = 3
UPLOAD_HINT = {
    "tiktok": (
        "TikTok's public oEmbed returns the caption and author only — no video, audio or "
        "transcript, and JARVIS doesn't scrape. To analyse what is said and shown, save the "
        "video in TikTok (Share → Save video, where the creator allows it) and drop the file "
        "here, or paste the transcript."
    ),
    "youtube": (
        "YouTube's oEmbed returns the title and channel only. Captions need the owner's "
        "authorisation. Drop a video file you're allowed to use, or paste the transcript."
    ),
    "instagram": (
        "Instagram's oEmbed needs a Meta app token, which JARVIS doesn't have. Drop the video "
        "file or paste the transcript."
    ),
    "other": (
        "JARVIS doesn't fetch arbitrary web pages. Drop the video, PDF or screenshot, or paste "
        "the text."
    ),
}


@dataclass(frozen=True)
class LinkInfo:
    provider: Provider
    canonical: str
    video_id: str | None
    short: bool
    host: str


@dataclass
class Resolution:
    link: LinkInfo
    capability: Capability
    fallback: Capability | None
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def _provider(host: str) -> Provider:
    for name, domains in DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name  # type: ignore[return-value]
    return "other"


_TIKTOK_VIDEO = re.compile(r"^/@([A-Za-z0-9._-]{1,64})/(?:video|photo)/(\d{5,25})/?$")
_TIKTOK_SHORT = re.compile(r"^/(?:t/)?([A-Za-z0-9]{5,20})/?$")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_link(raw: str) -> LinkInfo:
    text = (raw or "").strip()
    if len(text) > 2048 or any(c.isspace() for c in text):
        raise IntakeError("LINK_INVALID", "That isn't a single link.")
    if text.startswith("http://"):
        text = "https://" + text[len("http://") :]
    if not text.startswith("https://"):
        raise IntakeError("LINK_INVALID", "Only https links are accepted.")
    parts = urlsplit(text)
    if parts.username or parts.password:
        raise IntakeError("LINK_INVALID", "Links with credentials are refused.")
    if parts.port not in (None, 443):
        raise IntakeError("LINK_INVALID", "Links to custom ports are refused.")
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise IntakeError("LINK_INVALID", "Links to IP addresses are refused.")
    if not host or "." not in host:
        raise IntakeError("LINK_INVALID", "That link has no valid host.")
    provider = _provider(host)
    path = parts.path or "/"
    if provider == "tiktok":
        match = _TIKTOK_VIDEO.match(path)
        if match:
            user, number = match.groups()
            return LinkInfo(
                "tiktok", f"https://www.tiktok.com/@{user}/video/{number}", number, False, host
            )
        short = _TIKTOK_SHORT.match(path)
        if short and (host.startswith(("vm.", "vt.")) or path.startswith("/t/")):
            return LinkInfo("tiktok", f"https://{host}{path}", None, True, host)
        raise IntakeError("LINK_INVALID", "That TikTok link doesn't point to a video.")
    if provider == "youtube":
        vid: str | None = None
        if host.endswith("youtu.be"):
            vid = path.strip("/").split("/")[0]
        elif path.startswith("/shorts/"):
            vid = path.split("/")[2] if len(path.split("/")) > 2 else None
        elif path == "/watch":
            vid = (parse_qs(parts.query).get("v") or [""])[0] or None
        if not vid or not _YT_ID.match(vid):
            raise IntakeError("LINK_INVALID", "That YouTube link doesn't point to a video.")
        return LinkInfo("youtube", f"https://www.youtube.com/watch?v={vid}", vid, False, host)
    if provider == "instagram":
        match = re.match(r"^/(?:reel|reels|p)/([A-Za-z0-9_-]{5,40})/?$", path)
        if not match:
            raise IntakeError("LINK_INVALID", "That Instagram link doesn't point to a post.")
        vid = match.group(1)
        return LinkInfo("instagram", f"https://www.instagram.com/reel/{vid}/", vid, False, host)
    return LinkInfo("other", f"https://{host}{path}", None, False, host)


HostResolver = Callable[[str], Awaitable[list[str]]]


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    return bool(ip.is_global) and not ip.is_multicast


class LinkResolver:
    def __init__(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        resolve_host: HostResolver = system_resolver,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._transport = transport
        self._resolve_host = resolve_host
        self._timeout = timeout_seconds

    async def _check_host(self, host: str) -> None:
        if _provider(host) == "other":
            raise IntakeError("LINK_BLOCKED", f"{host} isn't on the allowlist.")
        try:
            addresses = await self._resolve_host(host)
        except OSError as exc:
            raise IntakeError("LINK_UNREACHABLE", f"{host} can't be resolved.") from exc
        if not addresses or not all(_public(a) for a in addresses):
            raise IntakeError("LINK_BLOCKED", f"{host} resolves to a non-public address.")

    async def _get(self, url: str) -> httpx2.Response:
        parts = urlsplit(url)
        await self._check_host((parts.hostname or "").lower())
        async with httpx2.AsyncClient(
            transport=self._transport, timeout=self._timeout, follow_redirects=False
        ) as client:
            async with client.stream(
                "GET", url, headers={"Accept": "application/json", "User-Agent": "JARVIS"}
            ) as response:
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE:
                        raise IntakeError("LINK_RESPONSE_TOO_LARGE", "The response is too large.")
                return httpx2.Response(response.status_code, headers=response.headers, content=body)

    async def _expand(self, link: LinkInfo) -> LinkInfo:
        url = link.canonical
        for _ in range(MAX_REDIRECTS):
            response = await self._get(url)
            location = response.headers.get("location")
            if response.status_code not in (301, 302, 303, 307, 308) or not location:
                break
            nxt = parse_link(
                location if location.startswith("http") else f"https://{link.host}{location}"
            )
            if nxt.provider != "tiktok":
                raise IntakeError("LINK_BLOCKED", "The short link redirects off TikTok.")
            if not nxt.short:
                return nxt
            url = nxt.canonical
        raise IntakeError("LINK_UNREACHABLE", "The short link didn't lead to a TikTok video.")

    async def resolve(self, link: LinkInfo) -> Resolution:
        hint = UPLOAD_HINT[link.provider]
        if link.provider not in OEMBED:
            return Resolution(link, "UNAVAILABLE", "REQUIRES_UPLOAD", notes=[hint])
        try:
            if link.short:
                link = await self._expand(link)
            response = await self._get(
                OEMBED[link.provider].format(url=quote(link.canonical, safe=""))
            )
            if response.status_code != 200:
                raise IntakeError(
                    "LINK_UNAVAILABLE",
                    f"The platform answered {response.status_code} (private, removed or "
                    "region-locked videos can't be read).",
                )
            data = json.loads(response.content.decode("utf-8"))
            if not isinstance(data, dict):
                raise IntakeError("LINK_UNAVAILABLE", "The platform's answer wasn't usable.")
        except IntakeError as exc:
            return Resolution(
                link, "UNAVAILABLE", "REQUIRES_UPLOAD", notes=[hint], error=exc.message
            )
        except (httpx2.TransportError, httpx2.TimeoutException) as exc:
            return Resolution(
                link,
                "UNAVAILABLE",
                "REQUIRES_UPLOAD",
                notes=[hint],
                error=f"The platform couldn't be reached ({type(exc).__name__}).",
            )
        except (ValueError, UnicodeDecodeError):
            return Resolution(
                link, "UNAVAILABLE", "REQUIRES_UPLOAD", notes=[hint], error="Unreadable answer."
            )
        metadata = {
            "title": str(data.get("title") or "")[:2200],
            "author_name": str(data.get("author_name") or "")[:200],
            "author_url": str(data.get("author_url") or "")[:300],
            "thumbnail_url": str(data.get("thumbnail_url") or "")[:600],
            "provider_name": str(data.get("provider_name") or link.provider)[:60],
            "embed_available": bool(data.get("html")),
        }
        capability: Capability = "EMBED_ONLY" if metadata["embed_available"] else "METADATA_ONLY"
        return Resolution(link, capability, "REQUIRES_UPLOAD", metadata=metadata, notes=[hint])
