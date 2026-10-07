import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { searchInvestigationsV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatDate, formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";

export function InvestigationsPage() {
  const url = useUrlState();
  const q = url.get("q");
  const [draft, setDraft] = useState(q);
  useEffect(() => setDraft(q), [q]);

  const found = useQuery({
    queryKey: ["investigations", q],
    queryFn: () => unwrap(searchInvestigationsV1({ query: { q } })),
    placeholderData: keepPreviousData
  });

  function search(event: FormEvent) {
    event.preventDefault();
    url.set({ q: draft.trim() });
  }

  return (
    <>
      <PageHeader title="Найти человека" instruction="Найдите человека и откройте его досье." />
      <form onSubmit={search} role="search" className="mb-4 flex flex-wrap gap-2">
        <Input
          type="search"
          aria-label="Имя или вариант написания"
          placeholder="Имя или вариант написания"
          className="w-72"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit">Найти</Button>
      </form>
      <QueryState query={found}>
        {(data) => (
          <>
            <h2 className="mb-2 text-lg font-semibold">{data.heading}</h2>
            {data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">Никого не найдено.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Человек</TableHead>
                    <TableHead>Роль</TableHead>
                    <TableHead>Публикаций</TableHead>
                    <TableHead>Последняя</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((item) => (
                    <TableRow key={item.key}>
                      <TableCell>
                        <Link className="underline" to={dossierPath(item.key)}>
                          {item.name}
                        </Link>
                      </TableCell>
                      <TableCell>{item.role_label ?? DASH}</TableCell>
                      <TableCell>{formatNumber(item.article_count)}</TableCell>
                      <TableCell>{formatDate(item.last_published_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </>
        )}
      </QueryState>
    </>
  );
}
