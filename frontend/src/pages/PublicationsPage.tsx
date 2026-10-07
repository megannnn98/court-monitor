import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listPublicationsV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { OptionSelect } from "@/components/OptionSelect";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatDate, formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";

export function PublicationsPage() {
  const url = useUrlState();
  const query = { q: url.get("q"), source: url.getNumber("source", 0), page: Math.max(1, url.getNumber("page", 1)) };
  const [draft, setDraft] = useState(query.q);
  useEffect(() => setDraft(query.q), [query.q]);

  const publications = useQuery({
    queryKey: ["publications", query],
    queryFn: () => unwrap(listPublicationsV1({ query })),
    placeholderData: keepPreviousData
  });

  function search(event: FormEvent) {
    event.preventDefault();
    url.set({ q: draft.trim(), page: null });
  }

  return (
    <>
      <PageHeader title="Публикации" instruction="Публикации с уголовными делами: источник, люди и события каждой." />
      <form onSubmit={search} role="search" className="mb-3 flex flex-wrap items-end gap-3">
        <Input
          type="search"
          aria-label="Слова из заголовка или текста"
          placeholder="Слова из заголовка или текста"
          className="w-72"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit">Найти</Button>
      </form>
      <QueryState query={publications}>
        {(data) => (
          <>
            <div className="mb-3">
              <OptionSelect
                label="Источник"
                value={String(query.source)}
                options={[
                  { value: "0", label: "Все источники" },
                  ...data.sources.map((source) => ({ value: String(source.id), label: source.name, count: source.count }))
                ]}
                className="w-72"
                onChange={(value) => url.set({ source: value === "0" ? null : value, page: null })}
              />
            </div>
            <p className="mb-2 text-sm text-muted-foreground">Найдено: {formatNumber(data.total)}.</p>
            {data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">Ничего не найдено.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Дата</TableHead>
                    <TableHead>Публикация</TableHead>
                    <TableHead>Люди</TableHead>
                    <TableHead>События</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell>{formatDate(row.published_at)}</TableCell>
                      <TableCell className="whitespace-normal">
                        <Link className="underline" to={`/articles/${row.id}`}>
                          {row.title}
                        </Link>
                        <div className="text-xs text-muted-foreground">{row.source}</div>
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        {row.people.length
                          ? row.people.map((person, index) => (
                              <span key={person.key}>
                                {index ? ", " : ""}
                                <Link className="underline" to={dossierPath(person.key)}>
                                  {person.name}
                                </Link>
                              </span>
                            ))
                          : DASH}
                        {row.more_people ? <span className="text-muted-foreground"> и ещё {row.more_people}</span> : null}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <div className="flex flex-wrap gap-1">
                          {row.events.length
                            ? row.events.map((event) => (
                                <Badge key={event.kind} variant="secondary">
                                  {event.label}
                                  {event.count > 1 ? `: ${event.count}` : ""}
                                </Badge>
                              ))
                            : DASH}
                        </div>
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
