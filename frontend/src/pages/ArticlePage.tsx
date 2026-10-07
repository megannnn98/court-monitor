import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";

import { getArticleMentionsV1, getArticleV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { unwrap } from "@/lib/api";
import { DASH, externalUrl, formatDateTime } from "@/lib/format";

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

/** Who the article names and what happened in it, as the legacy page shows above the text. */
function Mentions({ articleId }: { articleId: number }) {
  const mentions = useQuery({
    queryKey: ["articles", articleId, "mentions"],
    queryFn: () => unwrap(getArticleMentionsV1({ path: { article_id: articleId } }))
  });
  return (
    <QueryState query={mentions}>
      {({ people, events }) => (
        <dl className="mb-4 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 rounded-md border p-3 text-sm">
          <dt className="text-muted-foreground">Люди</dt>
          <dd>
            {people.length
              ? people.map((person, index) => (
                  <span key={person.key}>
                    {index ? ", " : ""}
                    <a className="underline" href={person.dossier_url}>
                      {person.name}
                    </a>
                  </span>
                ))
              : DASH}
          </dd>
          <dt className="text-muted-foreground">События</dt>
          <dd className="flex flex-wrap gap-1">
            {events.length
              ? events.map((event) => (
                  <Badge key={event.kind} variant="secondary">
                    {event.label}
                    {event.count > 1 ? `: ${event.count}` : ""}
                  </Badge>
                ))
              : DASH}
          </dd>
        </dl>
      )}
    </QueryState>
  );
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
            <Mentions articleId={articleId} />
            <article className="whitespace-pre-wrap leading-relaxed">
              <ArticleText text={item.text} start={offset(params.get("start"))} end={offset(params.get("end"))} />
            </article>
          </>
        );
      }}
    </QueryState>
  );
}
