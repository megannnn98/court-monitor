import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { decidePairV1, listPairsV1, type PairResponse, type PairSideResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
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
      await Promise.all([
        client.invalidateQueries({ queryKey: ["review", "pairs"] }),
        client.invalidateQueries({ queryKey: ["status"] })
      ]);
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
          «Один человек» сливает две сущности сразу и при каждой следующей сборке; «Разные люди» убирает пару из списка. Сброс всех
          решений — в{" "}
          <a className="underline" href="/ui/pairs">
            старом интерфейсе
          </a>
          .
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
          </>
        )}
      </QueryState>
    </>
  );
}
