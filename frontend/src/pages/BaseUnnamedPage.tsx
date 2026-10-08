import { useEffect } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { decideBaseUnnamedV1, listBaseUnnamedV1, type BaseUnnamedCardResponse } from "@/api/generated";
import { CourtHints } from "@/components/CourtHints";
import { PageHeader } from "@/components/layout/PageHeader";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { StatusTabs } from "@/components/StatusTabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
import { formatDate } from "@/lib/format";

const SAME = "same";
const DIFFERENT = "different";
const CLEAR = "clear";

function State({ card }: { card: BaseUnnamedCardResponse }) {
  if (card.state === "identified") {
    return <Badge className="border-emerald-500 bg-emerald-50 text-emerald-800" variant="outline">опознан: {card.identified_as}</Badge>;
  }
  if (card.state === "confirmed") {
    return (
      <>
        <Badge className="border-emerald-500 bg-emerald-50 text-emerald-800" variant="outline">опознан: {card.identified_as}</Badge>
        <span className="text-sm text-muted-foreground">этой записи сейчас нет среди кандидатов из перечня</span>
      </>
    );
  }
  if (card.state === "open") {
    return <Badge className="border-amber-400 bg-amber-50 text-amber-800" variant="outline">не разобран</Badge>;
  }
  return <Badge variant="outline">кандидаты отклонены</Badge>;
}

function Card({ card }: { card: BaseUnnamedCardResponse }) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: ({ candidate, decision }: { candidate: string; decision: string }) =>
      unwrap(decideBaseUnnamedV1({ body: { record: card.record, candidate, decision } })),
    onSuccess: async () => {
      await readAgainAfterDecision(client, ["base-unnamed"]);
    }
  });
  const say = (candidate: string, decision: string) => decide.mutate({ candidate, decision });

  return (
    <article id={`b-${card.record}`} aria-label={card.full_name} className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <State card={card} />
      </div>
      <p className="font-medium">{card.full_name}</p>
      <p className="text-sm text-muted-foreground">{card.facts}</p>
      <CourtHints courts={card.courts} total={card.courts_total} />
      {card.candidates.length ? (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">Кандидаты из перечня</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ФИО</TableHead>
                <TableHead>Дата рождения</TableHead>
                <TableHead>Место рождения</TableHead>
                <TableHead>Почему подходит</TableHead>
                <TableHead>Решение</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {card.candidates.map((item) => (
                <TableRow key={item.key}>
                  <TableCell className="whitespace-normal font-medium">
                    {item.full_name}
                    {item.decision === SAME ? (
                      <Badge className="ml-1 border-emerald-500 bg-emerald-50 text-emerald-800" variant="outline">
                        это он
                      </Badge>
                    ) : item.decision === DIFFERENT ? (
                      <Badge className="ml-1" variant="outline">
                        не он
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell>{formatDate(item.birth_date)}</TableCell>
                  <TableCell className="whitespace-normal">{item.birth_place}</TableCell>
                  <TableCell className="whitespace-normal text-xs">{item.reasons.join("; ")}</TableCell>
                  <TableCell className="space-x-1 whitespace-nowrap">
                    {item.decision !== SAME ? (
                      <Button size="sm" disabled={decide.isPending} onClick={() => say(item.key, SAME)}>
                        Это он
                      </Button>
                    ) : null}
                    {item.decision !== DIFFERENT ? (
                      <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => say(item.key, DIFFERENT)}>
                        Не он
                      </Button>
                    ) : null}
                    {item.decision ? (
                      <Button size="sm" variant="ghost" disabled={decide.isPending} onClick={() => say(item.key, CLEAR)}>
                        Отменить
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {card.total > card.candidates.length ? (
            <p className="text-xs text-muted-foreground">
              Показаны {card.candidates.length} из {card.total} подходящих записей перечня: сначала родившиеся в названном городе, затем точный возраст и самые новые.
            </p>
          ) : null}
        </div>
      ) : null}
      {card.state === "confirmed" && card.confirmed ? (
        <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => say(card.confirmed!, CLEAR)}>
          Отменить решение
        </Button>
      ) : null}
      {decide.isError ? <p className="text-sm text-destructive">{decide.error.message}</p> : null}
    </article>
  );
}

export function BaseUnnamedPage() {
  const url = useUrlState();
  const query = { status: url.get("status", "open"), page: Math.max(1, url.getNumber("page", 1)) };
  const result = useQuery({
    queryKey: ["base-unnamed", query],
    queryFn: () => unwrap(listBaseUnnamedV1({ query })),
    placeholderData: keepPreviousData
  });

  // A link to one record: shown once its card is drawn.
  const loaded = Boolean(result.data);
  useEffect(() => {
    if (loaded && window.location.hash) {
      document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView();
    }
  }, [loaded]);

  return (
    <>
      <PageHeader title="Без имени в базе" instruction="Записи базы Airtable без имени и кандидаты на них из перечня Росфинмониторинга." />
      <QueryState query={result}>
        {(data) => (
          <>
            <div className="flex items-start justify-between gap-4">
              <StatusTabs options={data.statuses} value={query.status} onChange={(value) => url.set({ status: value === "open" ? null : value, page: null })} />
              <Link className="shrink-0 pt-2 text-sm underline" to="/unnamed">
                Безымянные из новостей
              </Link>
            </div>
            {data.items.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                {data.none_yet
                  ? "Записей без имени с кандидатами из перечня нет. Нужны синхронизация с Airtable и загруженный перечень Росфинмониторинга."
                  : "В этом разделе никого."}
              </p>
            ) : (
              <div className="space-y-4">
                {data.items.map((card) => (
                  <Card key={card.record} card={card} />
                ))}
              </div>
            )}
            <Pager page={query.page} pageSize={data.page_size} shown={data.items.length} total={data.total} onPage={(next) => url.set({ page: next === 1 ? null : next })} />
            <details className="mt-4 text-sm text-muted-foreground">
              <summary className="cursor-pointer select-none">Как читать карточку</summary>
              <p className="mt-2">
                Записи базы Airtable без имени («34-летний уроженец Крыма») и кто из перечня может быть ими: того возраста на день возбуждения дела (±1 год), того пола, родился в названном городе
                или регионе. Включённые в перечень после возбуждения дела стоят выше. Первыми идут записи, у которых кандидат появился в перечне позже всех. Имя в базу вносит оператор; здесь
                решение только запоминается.
              </p>
            </details>
          </>
        )}
      </QueryState>
    </>
  );
}
