import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listPoliticalV1, markPoliticalDoneV1, type PoliticalRowResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
import { DASH, externalUrl, formatDate, formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";
import { cn } from "@/lib/utils";

/** «Обработано»: the person leaves the list until a later news brings them back. */
function DoneBox({ row }: { row: PoliticalRowResponse }) {
  const client = useQueryClient();
  const mark = useMutation({
    mutationFn: (done: boolean) => unwrap(markPoliticalDoneV1({ body: { key: row.key, done } })),
    onSuccess: async () => {
      await readAgainAfterDecision(client, ["political"]);
    }
  });
  return (
    <div className="flex flex-col items-start gap-1">
      <Checkbox
        aria-label={`Обработано: ${row.name}`}
        title="Обработано"
        checked={row.done}
        disabled={mark.isPending}
        onCheckedChange={(checked) => mark.mutate(checked === true)}
      />
      {mark.isError ? <span className="text-xs text-destructive">{mark.error.message}</span> : null}
    </div>
  );
}

function Name({ row }: { row: PoliticalRowResponse }) {
  // A figurant the text does not name: their sentences on the legacy «Безымянные».
  return row.unnamed ? (
    <a className="font-medium underline" href={row.url}>
      {row.name}
    </a>
  ) : (
    <Link className="font-medium underline" to={dossierPath(row.key)}>
      {row.name}
    </Link>
  );
}

function Articles({ row }: { row: PoliticalRowResponse }) {
  if (!row.articles.length) {
    return <>{DASH}</>;
  }
  return (
    <>
      {row.articles.map((item, position) => (
        <span key={item.article}>
          {position ? ", " : ""}
          {item.political ? <b>{item.article}</b> : item.article}
        </span>
      ))}
    </>
  );
}

/** The few words next to a name that decide what to do with the person. */
function Marks({ row }: { row: PoliticalRowResponse }) {
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {row.unnamed ? <Badge variant="outline" className="border-amber-400 bg-amber-50 text-amber-800">без имени</Badge> : null}
      {row.not_in_base ? <Badge variant="outline" className="border-sky-400 bg-sky-50 text-sky-800">нет в базе</Badge> : null}
      {row.known ? <Badge variant="outline">{row.known.label}</Badge> : null}
      {row.rf_label ? <Badge variant="outline" className="border-rose-400 bg-rose-50 text-rose-800">{row.rf_label}</Badge> : null}
      {row.done ? <Badge variant="outline">обработано</Badge> : null}
    </div>
  );
}

function News({ row }: { row: PoliticalRowResponse }) {
  if (!row.news_label) {
    return <>{DASH}</>;
  }
  const tone =
    row.news_kind === "new_case"
      ? "border-emerald-500 bg-emerald-50 text-emerald-800"
      : row.news_kind === "sentence"
        ? "border-violet-500 bg-violet-50 text-violet-800"
        : "";
  return (
    <Badge variant="outline" className={tone} title={row.news_reason || undefined}>
      {row.news_label}
    </Badge>
  );
}

// The column a queue needs beyond the common ones; the rest of a person is in the panel.
const EXTRA: Record<string, { head: string; cell: (row: PoliticalRowResponse) => ReactNode }> = {
  unnamed: {
    head: "Похожие в базе Airtable",
    cell: (row) => (row.known ? <span className="text-xs text-muted-foreground">{row.known.names.join("; ")}</span> : DASH)
  },
  awaited: {
    head: "Перечень РФМ",
    cell: (row) => <span className={row.awaited ? "font-medium text-amber-700" : "text-muted-foreground"}>{row.listing || DASH}</span>
  }
};

/** Everything of one person the table leaves out. */
function Panel({ row }: { row: PoliticalRowResponse }) {
  return (
    <aside aria-label={`Подробно: ${row.name}`} className="space-y-4 rounded-lg border bg-card p-4 text-sm xl:sticky xl:top-4">
      <div>
        <div className="text-base">
          <Name row={row} />
        </div>
        <Marks row={row} />
        <div className="mt-1 text-muted-foreground">
          {row.regions || DASH} · {formatDate(row.first_published_at)} — {formatDate(row.last_published_at)}
        </div>
      </div>
      <section>
        <h3 className="mb-1 font-medium">Почему политическое</h3>
        <p>{row.basis || DASH}</p>
        {row.basis_quote ? <p className="mt-1 text-muted-foreground">{row.basis_quote}</p> : null}
      </section>
      {row.known ? (
        <section>
          <h3 className="mb-1 font-medium">База Airtable: {row.known.label}</h3>
          <p className="text-muted-foreground">{row.known.names.join("; ")}</p>
        </section>
      ) : null}
      {row.rf_entry || row.listing || row.rf_included ? (
        <section>
          <h3 className="mb-1 font-medium">Перечень РФМ</h3>
          {row.rf_entry ? <p className="text-muted-foreground">{row.rf_entry}</p> : null}
          {row.rf_included ? <p className="text-muted-foreground">{row.rf_included}</p> : null}
          {row.listing ? <p className={row.awaited ? "font-medium text-amber-700" : "text-muted-foreground"}>{row.listing}</p> : null}
        </section>
      ) : null}
      {row.memorial ? (
        <section>
          <h3 className="mb-1 font-medium">Мемориал</h3>
          <p>{row.memorial}</p>
        </section>
      ) : null}
      <section>
        <h3 className="mb-1 font-medium">Публикации ({row.links.length})</h3>
        <ul className="space-y-1">
          {row.links.map((link) => {
            const href = externalUrl(link.url);
            return (
              <li key={link.url}>
                <span className="text-muted-foreground">{link.source}:</span>{" "}
                {href ? (
                  <a className="underline" href={href} rel="noopener noreferrer" target="_blank">
                    {link.title}
                  </a>
                ) : (
                  link.title
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </aside>
  );
}

/** The legacy page's paragraph, folded away: read once, not every day. */
function Help() {
  return (
    <details className="mt-4 text-sm text-muted-foreground">
      <summary className="cursor-pointer select-none">Как читать список</summary>
      <ul className="mt-2 list-disc space-y-1 pl-5">
        <li>Галочка — «обработано»: человек уходит из списка и вернётся, когда о нём появится новая новость.</li>
        <li>«Без имени» — фигуранты, которых публикация не называет; несколько упоминаний сведены по возрасту, полу и месту.</li>
        <li>«Возможно в перечне» — совпали только имя и фамилия, может быть тёзка.</li>
        <li>«Ждём в перечне» — статья такая, по которой включают в перечень, а записи пока нет.</li>
        <li>Жирная статья — из списка политических.</li>
        <li>База Airtable сверяется по имени: «вероятно» и «тёзки» — сигнал проверить запись, не уверенность.</li>
      </ul>
    </details>
  );
}

export function PoliticalPage() {
  const url = useUrlState();
  const query = {
    months: url.getNumber("months", 0),
    date_from: url.get("date_from"),
    date_to: url.get("date_to"),
    queue: url.get("queue", "all"),
    page: Math.max(1, url.getNumber("page", 1))
  };
  const custom = Boolean(query.date_from || query.date_to);
  const [dates, setDates] = useState({ from: query.date_from, to: query.date_to });
  useEffect(() => setDates({ from: query.date_from, to: query.date_to }), [query.date_from, query.date_to]);
  const [selected, setSelected] = useState<string | null>(null);

  const result = useQuery({
    queryKey: ["political", query],
    queryFn: () => unwrap(listPoliticalV1({ query })),
    placeholderData: keepPreviousData
  });

  function showDates(event: FormEvent) {
    event.preventDefault();
    url.set({ date_from: dates.from, date_to: dates.to, months: null, page: null });
  }

  const extra = EXTRA[query.queue];
  return (
    <>
      <PageHeader title="Результат" instruction="Люди, против которых заведены политические уголовные дела." />
      <QueryState query={result}>
        {(data) => {
          const shown = data.items.find((row) => row.key === selected) ?? data.items[0];
          return (
            <>
              <nav aria-label="Очереди" className="mb-1 flex flex-wrap border-b">
                {data.queues.map((queue) => {
                  const active = queue.value === query.queue;
                  return (
                    <button
                      key={queue.value}
                      type="button"
                      aria-pressed={active}
                      onClick={() => url.set({ queue: queue.value === "all" ? null : queue.value, page: null })}
                      className={cn(
                        "-mb-px border-b-2 px-4 py-2 text-sm",
                        active ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {queue.label}
                      <span className="ml-1 rounded-full bg-muted px-2 text-xs">{formatNumber(queue.count ?? 0)}</span>
                    </button>
                  );
                })}
              </nav>
              <div className="mb-3 flex flex-wrap items-center gap-2 py-2">
                {data.periods.map((period) => {
                  const active = Number(period.value) === query.months && !custom;
                  return (
                    <Button
                      key={period.value}
                      size="sm"
                      variant={active ? "default" : "ghost"}
                      aria-pressed={active}
                      onClick={() => url.set({ months: period.value === "0" ? null : period.value, date_from: null, date_to: null, page: null })}
                    >
                      {period.label}
                    </Button>
                  );
                })}
                <form onSubmit={showDates} className="flex items-center gap-1">
                  <Label htmlFor="date-from" className="text-sm">
                    с
                  </Label>
                  <Input id="date-from" type="date" className="h-8 w-36" value={dates.from} onChange={(event) => setDates({ ...dates, from: event.target.value })} />
                  <Label htmlFor="date-to" className="text-sm">
                    по
                  </Label>
                  <Input id="date-to" type="date" className="h-8 w-36" value={dates.to} onChange={(event) => setDates({ ...dates, to: event.target.value })} />
                  <Button type="submit" size="sm" variant="outline">
                    Показать
                  </Button>
                </form>
                <span className="ml-auto text-sm text-muted-foreground">
                  Найдено: {formatNumber(data.total)} ·{" "}
                  <a className="underline" href={data.export_url}>
                    Скачать Excel
                  </a>
                </span>
              </div>
              {data.items.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">За этот период в этой очереди никого нет.</p>
              ) : (
                <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_20rem] 2xl:grid-cols-[minmax(0,1fr)_24rem]">
                  <div>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-8" title="Обработано">
                            ✓
                          </TableHead>
                          <TableHead>Человек</TableHead>
                          <TableHead>Новость</TableHead>
                          <TableHead>Статьи УК</TableHead>
                          {/* A queue's own column takes the region's place: both do not fit beside the
                              panel, and the panel names the region. */}
                          {extra ? (
                            <TableHead className="max-w-36 leading-tight whitespace-normal">{extra.head}</TableHead>
                          ) : (
                            <TableHead>Регион</TableHead>
                          )}
                          {/* Two lines, so the date's column is as narrow as a date: the table must fit beside the panel. */}
                          <TableHead className="w-24 leading-tight whitespace-normal">Последняя новость</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.items.map((row) => (
                          <TableRow
                            key={row.key}
                            aria-selected={row.key === shown?.key}
                            onClick={() => setSelected(row.key)}
                            className={cn("cursor-pointer", row.key === shown?.key && "bg-muted", row.done && "opacity-60")}
                          >
                            <TableCell onClick={(event) => event.stopPropagation()}>
                              <DoneBox row={row} />
                            </TableCell>
                            <TableCell className="whitespace-normal">
                              <Name row={row} />
                              <Marks row={row} />
                            </TableCell>
                            <TableCell>
                              <News row={row} />
                            </TableCell>
                            <TableCell className="max-w-32 whitespace-normal">
                              <Articles row={row} />
                            </TableCell>
                            {extra ? (
                              <TableCell className="max-w-48 whitespace-normal">{extra.cell(row)}</TableCell>
                            ) : (
                              <TableCell className="max-w-32 truncate" title={row.regions}>
                                {row.regions || DASH}
                              </TableCell>
                            )}
                            <TableCell>{formatDate(row.last_published_at)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    <Pager
                      page={query.page}
                      pageSize={data.page_size}
                      shown={data.items.length}
                      total={data.total}
                      onPage={(next) => url.set({ page: next === 1 ? null : next })}
                    />
                  </div>
                  {shown ? <Panel row={shown} /> : null}
                </div>
              )}
              <Help />
            </>
          );
        }}
      </QueryState>
    </>
  );
}
