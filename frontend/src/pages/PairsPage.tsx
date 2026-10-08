import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { decidePairV1, listPairsV1, type PairResponse, type PairSideResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmButton } from "@/components/ConfirmButton";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useUrlState } from "@/hooks/useUrlState";
import { ApiError, unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
import { formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";

function Side({ side }: { side: PairSideResponse }) {
  return (
    <div className="space-y-1 text-sm">
      <h3 className="font-medium">
        <Link className="underline" to={dossierPath(side.key)}>
          {side.name}
        </Link>{" "}
        {side.role_label ? <Badge variant="outline">{side.role_label}</Badge> : null}{" "}
        {side.rf_label ? <Badge variant="outline">{side.rf_label}</Badge> : null}
      </h3>
      <p className="text-muted-foreground">Как писали: {side.variants.map((form) => `${form.form} (${form.count})`).join(", ")}</p>
      <p>
        Упоминаний: {formatNumber(side.mention_count)} · публикаций: {formatNumber(side.article_count)}
      </p>
      {side.regions.length ? <p>Регион: {side.regions.join(", ")}</p> : null}
      {side.articles.length ? <p>Статьи УК: {side.articles.map((item) => item.article).join(", ")}</p> : null}
    </div>
  );
}

function Pair({ pair }: { pair: PairResponse }) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (decision: "same" | "different") =>
      unwrap(decidePairV1({ body: { key_a: pair.left.key, key_b: pair.right.key, decision } })),
    onSuccess: async () => {
      await readAgainAfterDecision(client, ["review", "pairs"]);
    }
  });
  return (
    <Card>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">{pair.hint}</p>
        {pair.note ? <p className="text-sm text-amber-700">{pair.note}</p> : null}
        <div className="grid gap-4 md:grid-cols-2">
          <Side side={pair.left} />
          <Side side={pair.right} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate("same")}>
            Один человек
          </Button>
          <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate("different")}>
            Разные люди
          </Button>
          {decide.isError ? <span className="text-xs text-destructive">{decide.error.message}</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}

/** «Сбросить все решения по парам», as on the legacy page: the operator starts the pairs
 * over. Sent to the legacy route, which answers with how many decisions it forgot. */
function Reset({ decided }: { decided: Record<string, number> }) {
  const client = useQueryClient();
  const total = Object.values(decided).reduce((sum, count) => sum + count, 0);
  const reset = useMutation({
    mutationFn: async () => {
      const answer = await fetch("/ui/pairs/reset-decisions", { method: "POST" });
      if (!answer.ok) {
        throw new ApiError(`Сервер ответил ${answer.status}`, answer.status);
      }
      return new URL(answer.url, window.location.origin).searchParams.get("reset") ?? String(total);
    },
    onSuccess: async () => {
      await readAgainAfterDecision(client, ["review", "pairs"]);
    }
  });
  if (!total && !reset.isSuccess) {
    return <p className="mt-4 text-sm text-muted-foreground">Сохранённых решений по парам нет.</p>;
  }
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
      {total ? (
        <ConfirmButton
          variant="destructive"
          title="Сбросить все решения по парам?"
          question={`Удалить все решения по спорным парам (${total}, из них вручную ${decided.manual ?? 0})? Пары «Разные люди» вернутся сразу, слитые люди разделятся при следующей сборке сущностей. Это необратимо.`}
          confirm="Сбросить"
          disabled={reset.isPending}
          onConfirm={() => reset.mutate()}
        >
          Сбросить все решения по парам
        </ConfirmButton>
      ) : null}
      {total ? (
        <span className="text-muted-foreground">
          Сохранено решений: {total} (вручную: {decided.manual ?? 0}, по перечню: {decided.rf ?? 0}, по региону: {decided.region ?? 0}).
        </span>
      ) : null}
      {reset.isSuccess ? <span role="status">Решения по парам сброшены: {reset.data}.</span> : null}
      {reset.isError ? <span className="text-destructive">{reset.error.message}</span> : null}
    </div>
  );
}

export function PairsPage() {
  const url = useUrlState();
  const query = { kind: url.get("kind", "all"), page: Math.max(1, url.getNumber("page", 1)), key: url.get("key") };
  const pairs = useQuery({
    queryKey: ["review", "pairs", query],
    queryFn: () => unwrap(listPairsV1({ query })),
    placeholderData: keepPreviousData
  });

  return (
    <>
      <PageHeader
        title="Спорные совпадения людей"
        instruction="Сущности, которые могут быть одним человеком: реестр пишет ФИО с отчеством, новости — без; имя бывает записано по-разному («Лида» и «Лидия»)."
      >
        <p className="text-sm text-muted-foreground">
          «Один человек» сливает две сущности сразу и при каждой следующей сборке; «Разные люди» убирает пару из списка.
        </p>
      </PageHeader>
      <QueryState query={pairs}>
        {(data) => (
          <>
            <div className="mb-3 flex flex-wrap gap-2">
              {data.kinds.map((kind) => (
                <Button
                  key={kind.value}
                  size="sm"
                  variant={kind.value === query.kind ? "default" : "outline"}
                  onClick={() => url.set({ kind: kind.value === "all" ? null : kind.value, page: null })}
                >
                  {kind.label} ({kind.count})
                </Button>
              ))}
            </div>
            <p className="mb-3 text-sm text-muted-foreground">Нерешённых пар: {formatNumber(data.total)}.</p>
            {data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">Спорных пар нет.</p>
            ) : (
              <div className="space-y-4">
                {data.items.map((pair) => (
                  <Pair key={`${pair.left.key}|${pair.right.key}`} pair={pair} />
                ))}
              </div>
            )}
            <Pager
              page={query.page}
              pageSize={data.page_size}
              shown={data.items.length}
              total={data.total}
              onPage={(next) => url.set({ page: next === 1 ? null : next })}
            />
            <Reset decided={data.decided} />
          </>
        )}
      </QueryState>
    </>
  );
}
