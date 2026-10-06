"""In-memory rate limiting for the signup pages. Second line of defence behind nginx limit_req."""
from __future__ import annotations

from collections import deque
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
import time

from aiohttp.web import Request

WINDOW = 3600.0


def parse_networks(values: list[str]) -> list[IPv4Network | IPv6Network]:
    return [ip_network(v, strict=False) for v in values or []]


def client_ip(request: Request, trusted_proxies: list[IPv4Network | IPv6Network]) -> str:
    """request.remote, or the last X-Forwarded-For hop if request.remote is a trusted proxy
    (host nginx -> Docker gateway -> maubot)."""
    remote = request.remote or ""
    try:
        addr = ip_address(remote)
    except ValueError:
        return remote
    if any(addr in net for net in trusted_proxies):
        forwarded = request.headers.get("X-Forwarded-For", "")
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return remote


class RateLimiter:
    def __init__(self, per_ip_per_hour: int, global_per_hour: int, clock=time.monotonic) -> None:
        self.per_ip_per_hour = per_ip_per_hour
        self.global_per_hour = global_per_hour
        self.clock = clock
        self.per_ip: dict[str, deque[float]] = {}
        self.all: deque[float] = deque()

    @staticmethod
    def _prune(hits: deque[float], cutoff: float) -> None:
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def allow(self, ip: str) -> bool:
        """Sliding one-hour window per IP and globally. Only allowed requests are counted."""
        now = self.clock()
        cutoff = now - WINDOW
        self._prune(self.all, cutoff)
        hits = self.per_ip.setdefault(ip, deque())
        self._prune(hits, cutoff)
        if len(hits) >= self.per_ip_per_hour or len(self.all) >= self.global_per_hour:
            return False
        hits.append(now)
        self.all.append(now)
        if len(self.per_ip) > 10_000:
            for key in [k for k, v in self.per_ip.items() if not v]:
                del self.per_ip[key]
        return True
