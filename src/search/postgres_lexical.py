from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import ParsedArticleRecord, Source, SourceDocument
from sources.models import SearchHit, SearchQuery


class PostgresLexicalSearch:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def search(self, query: SearchQuery) -> list[SearchHit]:
        search_vector = ParsedArticleRecord.search_vector
        search_query = func.websearch_to_tsquery("russian", query.text)

        score = func.ts_rank_cd(search_vector, search_query).label("score")

        with self._session_factory() as session:
            result = session.execute(
                select(
                    ParsedArticleRecord.id.label("article_id"),
                    Source.base_url.label("source_base_url"),
                    SourceDocument.external_id.label("external_id"),
                    ParsedArticleRecord.title.label("title"),
                    ParsedArticleRecord.published_at.label("published_at"),
                    SourceDocument.canonical_url.label("url"),
                    ParsedArticleRecord.text.label("text"),
                    score,
                )
                .select_from(ParsedArticleRecord)
                .join(SourceDocument, ParsedArticleRecord.document_id == SourceDocument.id)
                .join(
                    Source,
                    SourceDocument.source_id == Source.id,
                )
                .where(search_vector.op("@@")(search_query))
                .order_by(score.desc(), ParsedArticleRecord.id.asc())
                .limit(query.limit)
            )

            return [SearchHit(**row._mapping) for row in result]
