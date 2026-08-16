from models import ArticleChunk, ParsedArticle


class Chunker:
    def split(self, article: ParsedArticle) -> list[ArticleChunk]:
        paragraphs = [paragraph.strip() for paragraph in article.text.split("\n\n")]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        return [
            ArticleChunk(ordinal=ordinal, text=paragraph)
            for ordinal, paragraph in enumerate(paragraphs)
        ]
