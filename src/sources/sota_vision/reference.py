from urllib.parse import urljoin, urlsplit, urlunsplit

from sources.models import SourceReference

SOTA_VISION_BASE_URL = "https://sota.vision"
SOTA_VISION_NETLOC = "sota.vision"

_NON_ARTICLE_PATH_PREFIXES = (
    "/category/",
    "/page/",
    "/tag/",
    "/wp-content/",
    "/wp-json/",
    "/wp-admin/",
    "/feed/",
    "/author/",
)


def canonicalize_sota_vision_reference(url: str) -> SourceReference | None:
    absolute_url = urljoin(SOTA_VISION_BASE_URL, url)
    parsed = urlsplit(absolute_url)

    if parsed.netloc != SOTA_VISION_NETLOC:
        return None

    path = parsed.path

    if path in ("", "/"):
        return None

    if path.startswith(_NON_ARTICLE_PATH_PREFIXES):
        return None

    canonical_url = urlunsplit(
        (
            "https",
            SOTA_VISION_NETLOC,
            path,
            "",
            "",
        )
    )

    return SourceReference(external_id=path, url=canonical_url)
