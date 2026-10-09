from typing import Protocol

from monitor_core.model.document import ParsedArticle, RawDocument


class ArticleParser(Protocol):
    def parse(
        self,
        raw: RawDocument,
    ) -> ParsedArticle: ...
