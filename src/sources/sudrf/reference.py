import re
from urllib.parse import SplitResult, parse_qs, urljoin, urlsplit

from sources.models import SourceReference

NEWS_PATH = "/modules.php"
LISTING_QUERY = "name=press_dep"

_YEAR_PATTERN = re.compile(r"^\d{4}$")


def sudrf_host_aliases(host: str) -> frozenset[str]:
    """The canonical host and the `2zovs--msk.sudrf.ru` form it redirects from.

    An allowlist rather than a rewrite: rewriting every `--` would also accept
    `2zovs.msk.sudrf--ru`, a separately registrable domain.
    """
    return frozenset({host, host.replace(".", "--", 1)})


def court_url(host: str, query: str) -> str:
    return f"https://{host}{NEWS_PATH}?{query}"


def canonicalize_sudrf_reference(host: str, url: str) -> SourceReference | None:
    """Reference to one press-service news item, or None for any other link."""
    parsed = _split_court_url(host, url)

    if parsed is None:
        return None

    query = parse_qs(parsed.query)

    if query.get("name") != ["press_dep"] or query.get("op") != ["1"]:
        return None

    did = query.get("did")

    if did is None or not did[0].isdigit():
        return None

    return SourceReference(
        external_id=did[0],
        url=court_url(host, f"{LISTING_QUERY}&op=1&did={did[0]}"),
    )


def archive_year(host: str, url: str) -> str | None:
    """The year of a press-service archive page, or None for any other link."""
    parsed = _split_court_url(host, url)

    if parsed is None:
        return None

    query = parse_qs(parsed.query)

    if query.get("name") != ["press_dep"] or query.get("op") != ["12"]:
        return None

    arc_list = query.get("arc_list")

    if arc_list is None or _YEAR_PATTERN.match(arc_list[0]) is None:
        return None

    return arc_list[0]


def _split_court_url(host: str, url: str) -> SplitResult | None:
    """The parsed URL when it addresses this court's press-service module."""
    parsed = urlsplit(urljoin(f"https://{host}", url.strip()))

    if parsed.hostname not in sudrf_host_aliases(host):
        return None

    if parsed.path != NEWS_PATH:
        return None

    return parsed
