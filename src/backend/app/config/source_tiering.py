"""Domain tiering policy for source governance.

Policy goals:
- Prioritize trusted publishers (core)
- Keep variety via controlled rotation sources
- Allow long-tail discovery with tighter caps
- Hard-block obvious non-editorial sources
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
from urllib.parse import urlparse

from app.config.source_registry import is_shortlisted_source


class DomainTier(str, Enum):
    CORE = "core"
    ROTATION = "rotation"
    DISCOVERY = "discovery"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DomainPolicy:
    tier: DomainTier
    quality_weight: float
    daily_cap: int
    allow_signal_ingest: bool
    allow_direct_ingest: bool


_POLICIES: Dict[DomainTier, DomainPolicy] = {
    DomainTier.CORE: DomainPolicy(
        tier=DomainTier.CORE,
        quality_weight=0.90,
        daily_cap=4,
        allow_signal_ingest=True,
        allow_direct_ingest=True,
    ),
    DomainTier.ROTATION: DomainPolicy(
        tier=DomainTier.ROTATION,
        quality_weight=0.78,
        daily_cap=2,
        allow_signal_ingest=True,
        allow_direct_ingest=True,
    ),
    DomainTier.DISCOVERY: DomainPolicy(
        tier=DomainTier.DISCOVERY,
        quality_weight=0.62,
        daily_cap=1,
        allow_signal_ingest=True,
        allow_direct_ingest=False,
    ),
    DomainTier.BLOCKED: DomainPolicy(
        tier=DomainTier.BLOCKED,
        quality_weight=0.0,
        daily_cap=0,
        allow_signal_ingest=False,
        allow_direct_ingest=False,
    ),
}

# Most trusted domains (high quality and stable coverage).
CORE_DOMAINS = {
    "techcrunch.com",
    "bloomberg.com",
    "arstechnica.com",
    "theverge.com",
    "wsj.com",
    "cnbc.com",
    "reuters.com",
    "nytimes.com",
    "venturebeat.com",
    "theregister.com",
    "thenewstack.io",
    "infoq.com",
    "thehackernews.com",
    "securityweek.com",
    "darkreading.com",
    "bleepingcomputer.com",
    "coindesk.com",
    "theblock.co",
    "finextra.com",
    "openai.com",
    "anthropic.com",
    "blog.google",
    "cloud.google.com",
    "blog.cloudflare.com",
    "aws.amazon.com",
    "github.blog",
    "spectrum.ieee.org",
    "arxiv.org",
    "cncf.io",
    "atlassian.com",
    "datadoghq.com",
    "docker.com",
    "pulumi.com",
    "vercel.com",
    "huggingface.co",
}

# Secondary set with useful diversity but lower consistency.
ROTATION_DOMAINS = {
    "9to5mac.com",
    "hackernoon.com",
    "hackread.com",
    "helpnetsecurity.com",
    "csoonline.com",
    "infoworld.com",
    "pymnts.com",
    "paymentsdive.com",
    "saastr.com",
    "tomtunguz.com",
    "simonwillison.net",
    "testingcatalog.com",
    "platformengineering.org",
    "figma.com",
    "creativebloq.com",
    "designweek.co.uk",
    "creativeboom.com",
    "towardsdatascience.com",
    "a16z.news",
    "lesswrong.com",
    "netflixtechblog.com",
    "expo.dev",
    "go.dev",
    "macrumors.com",
    "tomsguide.com",
    "qawolf.com",
    "airops.com",
    "cursor.com",
    "huntress.com",
    "wiz.io",
    "workos.com",
    "carta.com",
    "metronome.com",
    "finance.yahoo.com",
    "miro.com",
    "bankingdive.com",
    "cyberscoop.com",
    "dropbox.tech",
    "engineering.fb.com",
}

# Explicit discovery domains we still want represented.
DISCOVERY_DOMAINS = {
    "productpicnic.beehiiv.com",
    "speedrun.substack.com",
    "cloudedjudgement.substack.com",
    "joereis.substack.com",
    "latent.space",
    "cutlefish.substack.com",
    "proofofconcept.pub",
    "seangoedecke.com",
    "elenaverna.com",
    "warc.com",
    "withpersona.com",
    "getunblocked.com",
    "depthfirst.com",
    "qa.tech",
    "go.clerk.com",
}

BLOCKED_DOMAINS = {
    "advertise.tldr.tech",
    "github.com",
    "linkedin.com",
    "medium.com",
    "threadreaderapp.com",
    "x.com",
    "youtube.com",
    "findarticles.com",
}

_MULTI_LABEL_SUFFIXES = {
    "co.uk",
    "org.uk",
    "gov.uk",
    "ac.uk",
    "co.jp",
    "com.au",
    "com.br",
}

_TECH_HINTS = {
    "ai",
    "cloud",
    "code",
    "crypto",
    "cyber",
    "data",
    "design",
    "dev",
    "eng",
    "fin",
    "ml",
    "product",
    "security",
    "software",
    "stack",
    "startup",
    "tech",
}


def normalize_domain(domain_or_url: str) -> str:
    """Normalize domain or URL into a lowercase hostname."""
    if not domain_or_url:
        return ""

    value = domain_or_url.strip().lower()
    parsed = urlparse(value)
    host = parsed.netloc or ""
    if not host:
        parsed = urlparse(f"https://{value}")
        host = parsed.netloc or parsed.path

    host = host.split("@")[-1].split(":")[0].strip(".")
    while host.startswith("www."):
        host = host[4:]

    return host


def registered_domain(domain_or_url: str) -> str:
    """Best-effort registrable domain extraction for lookup fallback."""
    host = normalize_domain(domain_or_url)
    if not host:
        return ""

    labels = host.split(".")
    if len(labels) <= 2:
        return host

    suffix_two = ".".join(labels[-2:])
    if suffix_two in _MULTI_LABEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])

    return ".".join(labels[-2:])


def _match_domain_set(domain: str, candidates: set[str]) -> Optional[str]:
    """Return matching domain from set via exact or root-domain fallback."""
    if domain in candidates:
        return domain

    root = registered_domain(domain)
    if root in candidates:
        return root

    # Also allow candidate suffix matching for curated subdomains.
    for candidate in candidates:
        if domain.endswith(f".{candidate}"):
            return candidate

    return None


def _looks_like_tech_domain(domain: str) -> bool:
    parts = domain.replace("-", ".").split(".")
    return any(part in _TECH_HINTS for part in parts)


def get_domain_tier(domain_or_url: str) -> DomainTier:
    """Classify a domain into core/rotation/discovery/blocked."""
    domain = normalize_domain(domain_or_url)
    if not domain:
        return DomainTier.BLOCKED

    if _match_domain_set(domain, BLOCKED_DOMAINS):
        return DomainTier.BLOCKED
    if _match_domain_set(domain, CORE_DOMAINS):
        return DomainTier.CORE
    if _match_domain_set(domain, ROTATION_DOMAINS):
        return DomainTier.ROTATION
    if _match_domain_set(domain, DISCOVERY_DOMAINS):
        return DomainTier.DISCOVERY
    if is_shortlisted_source(domain):
        return DomainTier.ROTATION

    # Unknown domains default to discovery, with low trust/cap.
    if _looks_like_tech_domain(domain):
        return DomainTier.DISCOVERY

    return DomainTier.DISCOVERY


def get_domain_policy(domain_or_url: str) -> DomainPolicy:
    """Get policy envelope for a domain/URL."""
    tier = get_domain_tier(domain_or_url)
    return _POLICIES[tier]


def is_allowed_domain(domain_or_url: str, *, channel: str = "signal") -> bool:
    """Check if domain is ingest-eligible for a specific channel."""
    policy = get_domain_policy(domain_or_url)
    if channel == "direct":
        return policy.allow_direct_ingest
    return policy.allow_signal_ingest


__all__ = [
    "DomainTier",
    "DomainPolicy",
    "CORE_DOMAINS",
    "ROTATION_DOMAINS",
    "DISCOVERY_DOMAINS",
    "BLOCKED_DOMAINS",
    "normalize_domain",
    "registered_domain",
    "get_domain_tier",
    "get_domain_policy",
    "is_allowed_domain",
]
