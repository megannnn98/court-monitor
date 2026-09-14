from urllib.parse import urljoin, urlsplit, urlunsplit

from sources.models import SourceReference

OVD_INFO_BASE_URL = "https://ovd.info"
OVD_INFO_NETLOC = "ovd.info"


def canonicalize_ovd_info_reference(url: str) -> SourceReference | None:
    absolute_url = urljoin(OVD_INFO_BASE_URL, url)
    parsed = urlsplit(absolute_url)

    if parsed.netloc != OVD_INFO_NETLOC:
        return None

    path = parsed.path

    if not path.startswith("/express-news/"):
        return None

    canonical_url = urlunsplit(
        (
            "https",
            OVD_INFO_NETLOC,
            path,
            "",
            "",
        )
    )

    return SourceReference(external_id=path, url=canonical_url)
