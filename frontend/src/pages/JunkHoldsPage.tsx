import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  holdHeldV1,
  junkAllHeldV1,
  junkHeldV1,
  listJunkHoldsV1,
  reextractHeldV1,
  releaseHeldV1,
  unreleaseHeldV1,
  type HeldActionResponse,
  type HeldArticleResponse
} from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { StatusTabs } from "@/components/StatusTabs";
import { Button } from "@/components/ui/button";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
import { formatDate } from "@/lib/format";

// The lists (`web.ui.junk_holds`).
const HELD = "held";
const MODEL_JUNK = "model_junk";
const RELEASED = "released";
const EMPTY: Record<string, string> = {
  [HELD]: "Ничего не ждёт вашего решения.",
  [MODEL_JUNK]: "Модель ничего не сочла мусором.",
  [RELEASED]: "Модель ничего не выпустила в работу.",
  junk: "Никто ничего не отметил как мусор."
};

type One = "junk" | "release" | "unrelease" | "hold" | "reextract";

/** A word on held articles; the lists, the queue and the counters read again. */
function useWord(onReleased: (article: number) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ action, articles }: { action: One | "junk-all"; articles: number[] }): Promise<HeldActionResponse> => {
      if (action === "junk-all") {
        return unwrap(junkAllHeldV1({ body: { articles } }));
      }
      const call = { junk: junkHeldV1, release: releaseHeldV1, unrelease: unreleaseHeldV1, hold: holdHeldV1, reextract: reextractHeldV1 }[action];
      return unwrap(call({ body: { article: articles[0] } }));
    },
    onSuccess: async (answer) => {
      if (answer.released) {
        onReleased(answer.articles[0]);
      }
      await readAgainAfterDecision(client, ["junk-holds"]);
    }
  });
}

function Card({ item, word }: { item: HeldArticleResponse; word: ReturnType<typeof useWord> }) {
  const say = (action: One) => word.mutate({ action, articles: [item.article_id] });
  return (
    <article id={`a-${item.article_id}`} aria-label={item.title ?? "Без заголовка"} className="space-y-2 rounded-lg border bg-card p-4">
      <h3 className="font-medium">
        <Link className="hover:underline" to={`/articles/${item.article_id}`}>
          {item.title ?? "Без заголовка"}
        </Link>
      </h3>
      <p className="text-sm text-muted-foreground">
        {formatDate(item.published_at)} · {item.source}
        {item.url ? (
          <>
            {" · "}
            <a className="underline" href={item.url} rel="noopener noreferrer" target="_blank">
              источник
            </a>
          </>
        ) : null}{" "}
        · оценка {item.score.toFixed(2)} (порог {item.cutoff.toFixed(2)}) · события извлечения: {item.events ?? "нет"}
      </p>
      <p className="text-sm">{item.start}…</p>
      <p className="text-sm text-muted-foreground">{item.reason}</p>
      {item.note ? (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {item.note_label}
          {item.note}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {item.status === HELD ? (
          <>
            <Button size="sm" disabled={word.isPending} onClick={() => say("release")}>
              Это дело — в работу
            </Button>
            <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("reextract")}>
              Извлечь заново
            </Button>
            <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("junk")}>
              Мусор
            </Button>
          </>
        ) : item.status === RELEASED ? (
          <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("unrelease")}>
            Мусор
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("hold")}>
            Вернуть на проверку
          </Button>
        )}
      </div>
    </article>
  );
}

export function JunkHoldsPage() {
  const url = useUrlState();
  const status = url.get("status");
  const page = Math.max(1, url.getNumber("page", 1));
  const query = { ...(status ? { status } : {}), page };
  const result = useQuery({
    queryKey: ["junk-holds", query],
    queryFn: () => unwrap(listJunkHoldsV1({ query })),
    placeholderData: keepPreviousData
  });
  const [released, setReleased] = useState<number | null>(null);
  const word = useWord(setReleased);

  return (
    <>
      <PageHeader title="Отсев" instruction="Статьи, которые очистка удалила бы, а модель отсева удержала." />
      <QueryState query={result}>
        {(data) => {
          const together = (data.status === HELD || data.status === MODEL_JUNK) && !word.isPending;
          return (
            <>
              <StatusTabs options={data.statuses} value={data.status} onChange={(value) => url.set({ status: value, page: null })} />
              {released ? (
                <p className="mb-3 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                  Статья{" "}
                  <Link className="underline" to={`/articles/${released}`}>
                    #{released}
                  </Link>
                  : найдено уголовное событие, она возвращена в работу — её возьмёт следующая сборка людей.
                </p>
              ) : null}
              {word.isError ? <p className="mb-3 text-sm text-destructive">{word.error.message}</p> : null}
              {data.status === MODEL_JUNK && data.article_ids.length ? (
                <Button className="mb-3" disabled={!together} onClick={() => word.mutate({ action: "junk-all", articles: data.article_ids })}>
                  Мусор — все {data.article_ids.length}
                </Button>
              ) : null}
              {data.stories.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">{EMPTY[data.status]}</p>
              ) : (
                <div className="space-y-4">
                  {data.stories.map((story) =>
                    story.articles.length === 1 ? (
                      <Card key={story.articles[0].article_id} item={story.articles[0]} word={word} />
                    ) : (
                      <section key={story.articles[0].article_id} aria-label="Одна новость" className="space-y-3 rounded-lg border-2 border-dashed border-primary/40 p-3">
                        <div className="flex flex-wrap items-center gap-3 text-sm">
                          <strong>Похоже на одну новость, публикаций: {story.articles.length}.</strong>
                          <span className="text-muted-foreground">Совпал человек или заголовок — проверьте, прежде чем убирать все.</span>
                          {data.status === HELD || data.status === MODEL_JUNK ? (
                            <Button size="sm" variant="outline" disabled={!together} onClick={() => word.mutate({ action: "junk-all", articles: story.articles.map((item) => item.article_id) })}>
                              Мусор — все {story.articles.length}
                            </Button>
                          ) : null}
                        </div>
                        {story.articles.map((item) => (
                          <Card key={item.article_id} item={item} word={word} />
                        ))}
                      </section>
                    )
                  )}
                </div>
              )}
              {/* Pages of whole stories, never cut: their count, not rows. */}
              {data.pages > 1 ? (
                <div className="mt-3 flex items-center gap-2 text-sm">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => url.set({ page: page - 1 === 1 ? null : page - 1 })}>
                    ← Назад
                  </Button>
                  <span className="text-muted-foreground">
                    Страница {page} из {data.pages}
                  </span>
                  <Button variant="outline" size="sm" disabled={page >= data.pages} onClick={() => url.set({ page: page + 1 })}>
                    Дальше →
                  </Button>
                </div>
              ) : null}
              <details className="mt-4 text-sm text-muted-foreground">
                <summary className="cursor-pointer select-none">Как это работает</summary>
                <p className="mt-2">
                  Статьи, в которых правила не нашли уголовного события, но отсев счёл их похожими на новость об уголовном деле. Каждую читает модель. Где она видит уголовное дело, статья уходит в
                  работу сама («Выпущено в работу»). Обычную уголовщину и не-дела модель складывает в «Модель считает мусором»: просмотрите причины и уберите всё одной кнопкой. «На проверке»
                  остаётся то, по чему модель не ответила.
                </p>
                <p className="mt-2">
                  «Это дело — в работу» берёт статью вопреки модели; «Мусор» у выпущенной статьи возвращает её под очистку. Статья удаляется при следующей очистке только после слова человека.
                </p>
              </details>
            </>
          );
        }}
      </QueryState>
    </>
  );
}
