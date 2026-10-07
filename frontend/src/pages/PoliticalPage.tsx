import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { listPoliticalV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { OptionSelect } from "@/components/OptionSelect";
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
import { DASH, externalUrl, formatDate, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export function PoliticalPage() {
  const url = useUrlState();
  const query = {
    months: url.getNumber("months", 0),
    date_from: url.get("date_from"),
    date_to: url.get("date_to"),
    news: url.get("news", "all"),
    known: url.get("known", "all"),
    done: url.get("done", "hide"),
    who: url.get("who", "all"),
    rfm: url.get("rfm", "all"),
    page: Math.max(1, url.getNumber("page", 1))
  };
  const custom = Boolean(query.date_from || query.date_to);
  const [dates, setDates] = useState({ from: query.date_from, to: query.date_to });
  useEffect(() => setDates({ from: query.date_from, to: query.date_to }), [query.date_from, query.date_to]);

  const result = useQuery({
    queryKey: ["political", query],
    queryFn: () => unwrap(listPoliticalV1({ query })),
    placeholderData: keepPreviousData
  });

  function showDates(event: FormEvent) {
    event.preventDefault();
    url.set({ date_from: dates.from, date_to: dates.to, months: null, page: null });
  }

  return (
    <>
      <PageHeader
        title="Результат"
        instruction="Люди, против которых заведены политические уголовные дела; перечень Росфинмониторинга подтверждает их личность."
      >
        <p className="text-sm text-muted-foreground">
          Отметка «обработано» и выгрузка в Excel пока в{" "}
          <a className="underline" href="/ui/political">
            старом интерфейсе
          </a>
          .
        </p>
      </PageHeader>
      <QueryState query={result}>
        {(data) => (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {data.periods.map((period) => {
                const active = Number(period.value) === query.months && !custom;
                return (
                  <Button
                    key={period.value}
                    size="sm"
                    variant={active ? "default" : "outline"}
                    aria-pressed={active}
                    onClick={() => url.set({ months: period.value === "0" ? null : period.value, date_from: null, date_to: null, page: null })}
                  >
                    {period.label}
                  </Button>
                );
              })}
            </div>
            <form onSubmit={showDates} className="mb-3 flex flex-wrap items-end gap-2">
              <div className="space-y-1">
                <Label htmlFor="date-from">с</Label>
                <Input id="date-from" type="date" className="w-40" value={dates.from} onChange={(event) => setDates({ ...dates, from: event.target.value })} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="date-to">по</Label>
                <Input id="date-to" type="date" className="w-40" value={dates.to} onChange={(event) => setDates({ ...dates, to: event.target.value })} />
              </div>
              <Button type="submit" variant="outline">
                Показать
              </Button>
            </form>
            <div className="mb-3 flex flex-wrap items-end gap-3">
              <OptionSelect label="Свежая новость" value={query.news} options={data.news} onChange={(value) => url.set({ news: value === "all" ? null : value, page: null })} />
              {data.base_loaded ? (
                <OptionSelect label="В базе Airtable" value={query.known} options={data.known} onChange={(value) => url.set({ known: value === "all" ? null : value, page: null })} />
              ) : null}
              <OptionSelect label="Кто" value={query.who} options={data.who} className="w-40" onChange={(value) => url.set({ who: value === "all" ? null : value, page: null })} />
              <OptionSelect label="Перечень РФМ" value={query.rfm} options={data.rfm} onChange={(value) => url.set({ rfm: value === "all" ? null : value, page: null })} />
              <label className="flex items-center gap-2 pb-2 text-sm">
                <Checkbox checked={query.done === "show"} onCheckedChange={(checked) => url.set({ done: checked === true ? "show" : null, page: null })} />
                Показать обработанных ({formatNumber(data.done_total)})
              </label>
            </div>
            <p className="mb-2 text-sm text-muted-foreground">
              Найдено: {formatNumber(data.total)}. «Возможно в перечне» — совпали только имя и фамилия, может быть тёзка. Жирная
              статья — из списка политических.
            </p>
            {data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">За этот период и с этими фильтрами никого нет.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>№</TableHead>
                    <TableHead>Фамилия Имя</TableHead>
                    <TableHead>Свежая новость</TableHead>
                    {data.base_loaded ? <TableHead>В базе Airtable</TableHead> : null}
                    <TableHead>Регион</TableHead>
                    <TableHead>Перечень РФМ</TableHead>
                    <TableHead>Статьи УК</TableHead>
                    <TableHead>Почему политическое</TableHead>
                    <TableHead>Мемориал</TableHead>
                    <TableHead>Первая новость</TableHead>
                    <TableHead>Последняя новость</TableHead>
                    <TableHead>Публикации</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row, index) => (
                    <TableRow key={row.key} className={cn(row.done && "opacity-60")}>
                      <TableCell>{(query.page - 1) * data.page_size + index + 1}</TableCell>
                      <TableCell className="whitespace-normal">
                        <a className="font-medium underline" href={row.url}>
                          {row.name}
                        </a>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {row.rf_label ? <Badge variant="outline">{row.rf_label}</Badge> : null}
                          {row.unnamed ? <Badge variant="outline">без имени</Badge> : null}
                          {row.done ? <Badge variant="outline">обработано</Badge> : null}
                        </div>
                        {row.rf_included ? <div className="text-xs text-muted-foreground">{row.rf_included}</div> : null}
                      </TableCell>
                      <TableCell title={row.news_reason || undefined}>{row.news_label ?? DASH}</TableCell>
                      {data.base_loaded ? (
                        <TableCell className="whitespace-normal">
                          {row.not_in_base ? (
                            <Badge variant="outline">нет в базе</Badge>
                          ) : row.known ? (
                            <>
                              <Badge variant="outline" title={row.known.names.join("; ")}>
                                {row.known.label}
                              </Badge>
                              {row.known.level === "namesakes" ? null : (
                                <div className="text-xs text-muted-foreground">{row.known.names.join("; ")}</div>
                              )}
                            </>
                          ) : (
                            DASH
                          )}
                        </TableCell>
                      ) : null}
                      <TableCell className="whitespace-normal">{row.regions || DASH}</TableCell>
                      <TableCell className="whitespace-normal">
                        <span className="text-muted-foreground">{row.rf_entry}</span>
                        {row.listing ? <div className={row.awaited ? "font-medium text-amber-700" : "text-muted-foreground"}>{row.listing}</div> : null}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        {row.articles.map((item, position) => (
                          <span key={item.article}>
                            {position ? ", " : ""}
                            {item.political ? <b>{item.article}</b> : item.article}
                          </span>
                        ))}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        {row.basis}
                        <div className="text-xs text-muted-foreground">{row.basis_quote}</div>
                      </TableCell>
                      <TableCell className="whitespace-normal">{row.memorial ?? ""}</TableCell>
                      <TableCell>{formatDate(row.first_published_at)}</TableCell>
                      <TableCell>{formatDate(row.last_published_at)}</TableCell>
                      <TableCell className="min-w-48 whitespace-normal">
                        {row.links.map((link) => {
                          const href = externalUrl(link.url);
                          return (
                            <div key={link.url} className="text-xs">
                              <span className="text-muted-foreground">{link.source}:</span>{" "}
                              {href ? (
                                <a className="underline" href={href} rel="noopener noreferrer" target="_blank">
                                  {link.title}
                                </a>
                              ) : (
                                link.title
                              )}
                            </div>
                          );
                        })}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
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
