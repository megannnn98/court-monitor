import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";

import { getArticleV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { unwrap } from "@/lib/api";
import { externalUrl, formatDateTime } from "@/lib/format";

/** The quoted span marked, as on the legacy page; an out-of-range span marks nothing. */
function ArticleText({ text, start, end }: { text: string; start: number | null; end: number | null }) {
  if (start === null || end === null || !(start <= end && end <= text.length)) {
    return <>{text}</>;
  }
  return (
    <>
      {text.slice(0, start)}
      <mark>{text.slice(start, end)}</mark>
      {text.slice(end)}
    </>
  );
}

function offset(raw: string | null): number | null {
  const value = raw === null || raw === "" ? Number.NaN : Number(raw);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export function ArticlePage() {
  const articleId = Number(useParams().articleId);
  const [params] = useSearchParams();
  const article = useQuery({
    queryKey: ["articles", articleId],
    queryFn: () => unwrap(getArticleV1({ path: { article_id: articleId } }))
  });

  return (
    <QueryState query={article}>
      {(item) => {
        const link = externalUrl(item.url);
        return (
          <>
            <PageHeader title={item.title} instruction="Полный текст публикации — первоисточник доказательств.">
              <p className="text-sm">
                {item.source_name} · {formatDateTime(item.published_at)} ·{" "}
                {link ? (
                  <a className="underline" href={link} rel="noopener noreferrer" target="_blank">
                    {link}
                  </a>
                ) : (
                  item.url
                )}
              </p>
            </PageHeader>
            <article className="whitespace-pre-wrap leading-relaxed">
              <ArticleText text={item.text} start={offset(params.get("start"))} end={offset(params.get("end"))} />
            </article>
          </>
        );
      }}
    </QueryState>
  );
}
