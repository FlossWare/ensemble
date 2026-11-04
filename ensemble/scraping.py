from __future__ import annotations

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx


MAX_URI_CHARS = 2048
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 15
USER_AGENT = "FlossWare-Ensemble/0.1 (+URI preview; contact site owner before crawling)"


class ScrapeError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class _ReadableHTML(HTMLParser):
    BLOCKED = {"script", "style", "noscript", "svg", "canvas", "template"}
    BREAKS = {"p", "div", "br", "hr", "li", "ul", "ol", "section", "article", "main", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.BLOCKED:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if not self.skip and tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCKED and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False
        if not self.skip and tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if not self.skip:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)

    def result(self):
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        text = "\n".join(line for line in lines if line).strip()
        return " ".join(" ".join(self.title_parts).split()), text, self.links


def _resolve_public_uri(uri: str) -> tuple[str, str, str]:
    if not isinstance(uri, str) or not uri.strip() or len(uri) > MAX_URI_CHARS:
        raise ScrapeError("invalid_uri", "uri must be a non-empty URL of at most 2048 characters")
    parsed = urlsplit(uri.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ScrapeError("invalid_uri", "only absolute HTTP and HTTPS URIs are supported")
    if parsed.username or parsed.password:
        raise ScrapeError("invalid_uri", "credentials in URIs are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ScrapeError("invalid_uri", "URI contains an invalid port") from exc
    if port not in (None, 80, 443):
        raise ScrapeError("invalid_uri", "only standard HTTP and HTTPS ports are allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.endswith(".local"):
        raise ScrapeError("blocked_destination", "local hostnames are not allowed", 403)
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
            target_port = port or (443 if parsed.scheme == "https" else 80)
            answers = socket.getaddrinfo(host, target_port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(ipaddress.ip_address(item[4][0]) for item in answers))
        except (OSError, UnicodeError) as exc:
            raise ScrapeError("dns_failure", "URI hostname could not be resolved", 400) from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ScrapeError("blocked_destination", "private, loopback, link-local, and reserved destinations are blocked", 403)
    normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    # The selected public address is pinned into the TCP backend. TLS and HTTP
    # still use the original hostname for certificate validation, SNI and Host.
    return normalized, host, str(addresses[0])


def _validate_public_uri(uri: str) -> str:
    return _resolve_public_uri(uri)[0]


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect to a validated IP while retaining the original hostname for TLS."""

    def __init__(self, hostname: str, address: str):
        self.hostname = hostname.rstrip(".").lower()
        self.address = address
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host.rstrip(".").lower() != self.hostname:
            raise OSError("pinned transport received an unexpected hostname")
        return await self.backend.connect_tcp(
            self.address, port, timeout, local_address, socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self.backend.connect_unix_socket(path, timeout, socket_options)

    async def sleep(self, seconds):
        return await self.backend.sleep(seconds)


def _pinned_transport(hostname: str, address: str) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    pool = transport._pool
    if not hasattr(pool, "_network_backend"):
        raise RuntimeError("installed HTTP transport does not support pinned DNS")
    pool._network_backend = _PinnedNetworkBackend(hostname, address)
    return transport


async def preview_uri(uri: str) -> dict:
    current = uri
    response = None
    for _ in range(MAX_REDIRECTS + 1):
        current, hostname, address = _resolve_public_uri(current)
        transport = _pinned_transport(hostname, address)
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(TIMEOUT_SECONDS),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html, text/plain;q=0.9"},
        ) as client:
            try:
                async with client.stream("GET", current) as streamed:
                    if streamed.status_code in {301, 302, 303, 307, 308}:
                        location = streamed.headers.get("location")
                        if not location:
                            raise ScrapeError("invalid_redirect", "redirect response has no Location header", 502)
                        current = urljoin(current, location)
                        continue
                    if streamed.status_code >= 400:
                        raise ScrapeError("upstream_http_error", f"source returned HTTP {streamed.status_code}", 502)
                    content_type = streamed.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                        raise ScrapeError("unsupported_content_type", f"unsupported source content type: {content_type or 'unknown'}", 415)
                    body = bytearray()
                    async for chunk in streamed.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ScrapeError("response_too_large", "source response exceeds 2 MiB", 413)
                    response = (bytes(body), content_type, dict(streamed.headers))
                    break
            except httpx.TimeoutException as exc:
                raise ScrapeError("upstream_timeout", "source did not respond within 15 seconds", 504) from exc
            except httpx.HTTPError as exc:
                raise ScrapeError("upstream_failure", "could not retrieve source URI", 502) from exc
        if response is not None:
            break
    if response is None:
        raise ScrapeError("too_many_redirects", "source exceeded the redirect limit", 400)

    raw, content_type, headers = response
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        title = headers.get("x-page-title", "")[:512]
        text = decoded.strip()
        links = []
        media_type = "text/plain"
    else:
        parser = _ReadableHTML()
        parser.feed(decoded)
        title, text, links = parser.result()
        media_type = "text/markdown"
    if not text:
        raise ScrapeError("empty_content", "no readable text was extracted from this URI", 422)
    canonical_links = []
    for link in links:
        absolute = urljoin(current, link)
        try:
            canonical_links.append(_validate_public_uri(absolute))
        except ScrapeError:
            continue
    return {
        "ok": True,
        "requested_uri": uri,
        "final_uri": current,
        "title": title[:512] or current,
        "media_type": media_type,
        "text": text[:1_000_000],
        "characters": len(text),
        "links": list(dict.fromkeys(canonical_links))[:200],
        "truncated": len(text) > 1_000_000,
        "preview_only": True,
    }
