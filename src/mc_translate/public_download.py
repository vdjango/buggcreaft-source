"""Bounded public CDN redirects shared by sampling and cache population."""
import asyncio
import ipaddress
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit


def public_download_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or len(value) > 8192):
        raise ValueError('Invalid sample download URL')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname == 'localhost' or parsed.hostname.endswith('.local'):
            raise ValueError('Private sample download blocked') from None
    else:
        if not address.is_global:
            raise ValueError('Private sample download blocked')
    return value


@asynccontextmanager
async def public_download_response(http, target, headers=None, timeout=1800):
    """Caller supplies a PublicResolver session; credentials stay on the first hop."""
    headers = dict(headers or {})
    async with asyncio.timeout(timeout):
        for _ in range(5):
            target = public_download_url(target)
            async with http.get(target, headers=headers, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        raise ValueError('Download redirect has no location')
                    following = public_download_url(urljoin(str(response.url), location))
                    if urlsplit(target).scheme == 'https' and urlsplit(following).scheme != 'https':
                        raise ValueError('Insecure download redirect')
                    if urlsplit(following).netloc != urlsplit(target).netloc:
                        headers = {k: v for k, v in headers.items()
                                   if k.lower() not in ('x-api-key', 'authorization', 'cookie')}
                    target = following
                    continue
                yield response
                return
        raise ValueError('Too many download redirects')
