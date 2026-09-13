from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from models import SourceReference

OVD_INFO_BASE_URL = "https://ovd.info"


class OvdInfoListingParser:
    def parse(self, html: bytes) -> list[SourceReference]:
        tree = HTMLParser(html)

        references: list[SourceReference] = []
        seen_external_ids: set[str] = set()

        for node in tree.css("a"):
            href = node.attributes.get("href")

            if href is None:
                continue

            reference = self._parse_reference(href)

            if reference is None:
                continue

            if reference.external_id in seen_external_ids:
                continue

            seen_external_ids.add(reference.external_id)
            references.append(reference)

        return references

    @staticmethod
    def _parse_reference(href: str) -> SourceReference | None:
        absolute_url = urljoin(OVD_INFO_BASE_URL, href)
        parsed = urlsplit(absolute_url)

        if parsed.netloc != "ovd.info":
            return None

        path = parsed.path

        if not path.startswith("/express-news/"):
            return None

        canonical_url = urlunsplit(
            (
                "https",
                "ovd.info",
                path,
                "",
                "",
            )
        )

        return SourceReference(
            external_id=path,
            url=canonical_url,
        )
