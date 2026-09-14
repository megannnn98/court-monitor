from selectolax.parser import HTMLParser

from sources.models import SourceReference
from sources.ovd_info.reference import canonicalize_ovd_info_reference


class OvdInfoListingParser:
    def parse(self, html: bytes) -> list[SourceReference]:
        tree = HTMLParser(html)

        references: list[SourceReference] = []
        seen_external_ids: set[str] = set()

        for node in tree.css("a"):
            href = node.attributes.get("href")

            if href is None:
                continue

            reference = canonicalize_ovd_info_reference(href)

            if reference is None:
                continue

            if reference.external_id in seen_external_ids:
                continue

            seen_external_ids.add(reference.external_id)
            references.append(reference)

        return references
