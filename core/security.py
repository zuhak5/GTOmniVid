"""Security Shield & SSRF Guard for GTOmniVid.

Protects against:
1. Google Cloud Metadata service exfiltration (169.254.169.254).
2. Server-Side Request Forgery (SSRF) directed at internal VM ports, localhost, or RFC1918 LANs.
3. Arbitrary URL injection via strict domain whitelisting and protocol enforcement.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from typing import List, Optional
from urllib.parse import ParseResult, urlparse

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Base exception for all security violations."""
    pass


class InvalidURLError(SecurityError):
    """Raised when URL syntax is malformed."""
    pass


class ProtocolNotAllowedError(SecurityError):
    """Raised when protocol is not http or https."""
    pass


class DomainNotAllowedError(SecurityError):
    """Raised when the domain is not in the allowed platform whitelist."""
    pass


class SSRFError(SecurityError):
    """Raised when hostname resolves to a prohibited private, loopback, or GCP metadata IP."""
    pass


# Stage 2: Strict Regex Whitelist for Supported Media Platforms
ALLOWED_DOMAIN_PATTERNS = [
    re.compile(r"^(?:[a-zA-Z0-9-]+\.)?youtube\.com$", re.IGNORECASE),
    re.compile(r"^youtu\.be$", re.IGNORECASE),
    re.compile(r"^(?:[a-zA-Z0-9-]+\.)?tiktok\.com$", re.IGNORECASE),
    re.compile(r"^(?:[a-zA-Z0-9-]+\.)?instagram\.com$", re.IGNORECASE),
    re.compile(r"^instagr\.am$", re.IGNORECASE),
    re.compile(r"^(?:[a-zA-Z0-9-]+\.)?facebook\.com$", re.IGNORECASE),
    re.compile(r"^fb\.watch$", re.IGNORECASE),
    re.compile(r"^fb\.com$", re.IGNORECASE),
]

# Stage 3: Prohibited IP Networks (GCP Metadata, Loopback, RFC 1918 Private LANs)
PROHIBITED_IP_NETWORKS = [
    # GCP Metadata & Link-Local IPv4
    ipaddress.ip_network("169.254.0.0/16"),
    # IPv4 Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    # RFC 1918 Private Class A, B, C
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Carrier-Grade NAT (RFC 6598)
    ipaddress.ip_network("100.64.0.0/10"),
    # Current Network / Broadcast / Multicast
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    # IPv6 Loopback & Unspecified
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    # IPv6 Link-Local
    ipaddress.ip_network("fe80::/10"),
    # IPv6 Unique Local (ULA)
    ipaddress.ip_network("fc00::/7"),
    # IPv6 Multicast
    ipaddress.ip_network("ff00::/8"),
]


def validate_url_syntax_and_domain(url: str) -> ParseResult:
    """Validates URL protocol (http/https) and strict domain whitelist."""
    if not url or not isinstance(url, str):
        raise InvalidURLError("URL must be a non-empty string.")

    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception as err:
        raise InvalidURLError(f"Malformed URL structure: {err}") from err

    # Stage 1: Protocol Whitelist
    if parsed.scheme.lower() not in ("http", "https"):
        raise ProtocolNotAllowedError(
            f"Prohibited protocol '{parsed.scheme}'. Only 'http' and 'https' are allowed."
        )

    hostname = parsed.hostname
    if not hostname:
        raise InvalidURLError("URL does not contain a valid hostname.")

    hostname = hostname.lower()

    # Stage 2: Domain Whitelist Matching
    if not any(pattern.match(hostname) for pattern in ALLOWED_DOMAIN_PATTERNS):
        raise DomainNotAllowedError(
            f"Domain '{hostname}' is not a supported media platform. "
            f"Supported: YouTube, TikTok, Instagram, Facebook."
        )

    return parsed


async def resolve_hostname_ips(hostname: str) -> List[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolves hostname to IP addresses asynchronously."""
    loop = asyncio.get_running_loop()
    try:
        # getaddrinfo with family 0 queries both AF_INET and AF_INET6
        addr_info = await loop.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as err:
        raise SSRFError(f"DNS resolution failed for hostname '{hostname}': {err}") from err

    ips = []
    seen = set()
    for entry in addr_info:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        if ip_str not in seen:
            seen.add(ip_str)
            try:
                ips.append(ipaddress.ip_address(ip_str))
            except ValueError as err:
                raise SSRFError(f"Invalid resolved IP '{ip_str}': {err}") from err

    if not ips:
        raise SSRFError(f"No IP addresses resolved for hostname '{hostname}'.")

    return ips


def verify_ip_is_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Verifies that an IP does not reside in private, loopback, or GCP metadata blocks."""
    # Check against explicit prohibited subnets
    for network in PROHIBITED_IP_NETWORKS:
        if ip in network:
            raise SSRFError(
                f"Prohibited IP target '{ip}' matches restricted network '{network}' "
                f"(GCP metadata / internal LAN blocked)."
            )

    # General RFC validation
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise SSRFError(f"Prohibited non-public IP target: '{ip}'.")


async def validate_media_url(url: str) -> str:
    """Full 3-stage security filter pipeline.
    
    1. Validates scheme (http/https).
    2. Enforces platform domain whitelist.
    3. Resolves DNS and blocks GCP metadata / private network targets.
    
    Returns normalized clean URL string on success.
    """
    parsed = validate_url_syntax_and_domain(url)
    hostname = parsed.hostname
    assert hostname is not None

    # Stage 3: DNS pre-resolution & IP safety verification
    resolved_ips = await resolve_hostname_ips(hostname)
    for ip in resolved_ips:
        verify_ip_is_safe(ip)

    logger.debug("URL passed security validation: %s (resolved: %s)", url, resolved_ips)
    return url.strip()


def identify_platform(url: str) -> str:
    """Identifies the canonical platform name for a given URL."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    if "youtube.com" in hostname or "youtu.be" in hostname:
        return "youtube"
    if "tiktok.com" in hostname:
        return "tiktok"
    if "instagram.com" in hostname or "instagr.am" in hostname:
        return "instagram"
    if "facebook.com" in hostname or "fb.watch" in hostname or "fb.com" in hostname:
        return "facebook"

    return "unknown"
