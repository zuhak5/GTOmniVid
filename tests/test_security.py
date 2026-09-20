"""Unit tests for Security Shield & SSRF Guard (core/security.py)."""

import asyncio
import ipaddress
import pytest
from unittest.mock import patch

from core.security import (
    DomainNotAllowedError,
    InvalidURLError,
    ProtocolNotAllowedError,
    SSRFError,
    identify_platform,
    resolve_hostname_ips,
    validate_media_url,
    validate_url_syntax_and_domain,
    verify_ip_is_safe,
)


def test_valid_platform_domains():
    """Verify supported platform domain patterns pass syntax validation."""
    valid_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/shorts/xyz",
        "https://youtu.be/xyz123",
        "https://m.youtube.com/watch?v=test",
        "https://www.tiktok.com/@creator/video/1234567890",
        "https://tiktok.com/@user/video/9876543210",
        "https://www.instagram.com/reel/C123456/",
        "https://instagram.com/p/B123456/",
        "https://instagr.am/reel/abc/",
        "https://www.facebook.com/watch/?v=101587",
        "https://fb.watch/xyz123/",
        "https://fb.com/watch/?v=999"
    ]
    for url in valid_urls:
        parsed = validate_url_syntax_and_domain(url)
        assert parsed.hostname is not None


def test_invalid_protocol_rejection():
    """Verify non-HTTP(S) schemes are strictly rejected."""
    prohibited = [
        "ftp://youtube.com/video",
        "file:///etc/passwd",
        "gopher://youtube.com/",
        "javascript:alert(1)",
        "data:text/html,test"
    ]
    for url in prohibited:
        with pytest.raises((ProtocolNotAllowedError, InvalidURLError)):
            validate_url_syntax_and_domain(url)


def test_unsupported_domains():
    """Verify non-whitelisted domains raise DomainNotAllowedError."""
    unsupported = [
        "https://google.com/search?q=video",
        "https://vimeo.com/123456",
        "https://twitter.com/i/status/123",
        "https://x.com/user/status/123",
        "https://attacker-controlled.site/video.mp4"
    ]
    for url in unsupported:
        with pytest.raises(DomainNotAllowedError):
            validate_url_syntax_and_domain(url)


def test_ip_safety_verification_blocks_private_and_metadata():
    """Verify verify_ip_is_safe blocks loopback, RFC1918, link-local, and GCP metadata."""
    blocked_ips = [
        # GCP Metadata
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.0.1"),
        # Loopback
        ipaddress.ip_address("127.0.0.1"),
        ipaddress.ip_address("127.0.1.1"),
        ipaddress.ip_address("::1"),
        # RFC 1918 Private LANs
        ipaddress.ip_address("10.0.0.1"),
        ipaddress.ip_address("172.16.0.1"),
        ipaddress.ip_address("172.31.255.255"),
        ipaddress.ip_address("192.168.1.1"),
        ipaddress.ip_address("192.168.0.100"),
        # Carrier Grade NAT
        ipaddress.ip_address("100.64.0.1"),
        # IPv6 Link-Local and ULA
        ipaddress.ip_address("fe80::1"),
        ipaddress.ip_address("fc00::1"),
        ipaddress.ip_address("fd12:3456:789a::1")
    ]
    for ip in blocked_ips:
        with pytest.raises(SSRFError, match="Prohibited"):
            verify_ip_is_safe(ip)


def test_ip_safety_verification_allows_public_ips():
    """Verify safe public IPs pass verification without raising errors."""
    public_ips = [
        ipaddress.ip_address("8.8.8.8"),
        ipaddress.ip_address("1.1.1.1"),
        ipaddress.ip_address("142.250.190.46"),  # Google YouTube CDN
        ipaddress.ip_address("2607:f8b0:4004:c1b::be")  # Google IPv6
    ]
    for ip in public_ips:
        verify_ip_is_safe(ip)


@pytest.mark.asyncio
async def test_validate_media_url_catches_dns_pointing_to_metadata():
    """Verify validate_media_url blocks a valid-looking domain resolving to GCP metadata."""
    with patch(
        "core.security.resolve_hostname_ips",
        return_value=[ipaddress.ip_address("169.254.169.254")]
    ):
        with pytest.raises(SSRFError, match="GCP metadata"):
            await validate_media_url("https://youtube.com/watch?v=exploit")


@pytest.mark.asyncio
async def test_validate_media_url_passes_public_resolution():
    """Verify validate_media_url succeeds when hostname resolves to legitimate public IP."""
    with patch(
        "core.security.resolve_hostname_ips",
        return_value=[ipaddress.ip_address("142.250.190.46")]
    ):
        clean_url = await validate_media_url("https://www.youtube.com/watch?v=safe123")
        assert clean_url == "https://www.youtube.com/watch?v=safe123"


def test_identify_platform():
    """Verify canonical platform tagging."""
    assert identify_platform("https://youtu.be/xyz") == "youtube"
    assert identify_platform("https://www.youtube.com/watch?v=123") == "youtube"
    assert identify_platform("https://www.tiktok.com/@user/video/1") == "tiktok"
    assert identify_platform("https://www.instagram.com/reel/abc") == "instagram"
    assert identify_platform("https://fb.watch/test") == "facebook"
    assert identify_platform("https://facebook.com/reel/123") == "facebook"
    assert identify_platform("https://unknown.com/video") == "unknown"


@pytest.mark.asyncio
async def test_resolve_hostname_ips_timeout():
    """Verify resolve_hostname_ips raises SSRFError on DNS timeout."""
    async def mock_wait_for(fut, timeout):
        fut.close()
        raise asyncio.TimeoutError()

    with patch("asyncio.wait_for", side_effect=mock_wait_for):
        with pytest.raises(SSRFError, match="timed out"):
            await resolve_hostname_ips("tiktok.com")

