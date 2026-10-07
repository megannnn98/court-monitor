import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listPersonsV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { Pager } from "@/components/Pager";
import { isEmptyList, QueryState } from "@/components/QueryState";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH } from "@/lib/format";

const PAGE_SIZE = 100;
const ALL = "all";

export function PersonsPage() {
  const url = useUrlState();
  const page = Math.max(1, url.getNumber("page", 1));
  const status = url.get("status");

  const persons = useQuery({
    queryKey: ["persons", page, status],
    queryFn: () =>
      unwrap(listPersonsV1({ query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, status: status || null } }))
  });

  return (
    <>
      <PageHeader title="Персоны" instruction="Люди, которых собрало разрешение персон: имя, статус и слияния." />
      <div className="mb-4 space-y-1">
        <Label>Статус</Label>
        <Select value={status || ALL} onValueChange={(value) => url.set({ status: value === ALL ? null : value, page: null })}>
          <SelectTrigger className="w-48" aria-label="Статус">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Все</SelectItem>
            <SelectItem value="active">active</SelectItem>
            <SelectItem value="merged">merged</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <QueryState query={persons} isEmpty={(rows) => isEmptyList(rows) && page === 1} empty="Персон нет.">
        {(rows) => (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>Имя</TableHead>
                  <TableHead>Нормализованное</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead>Слит в</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((person) => (
                  <TableRow key={person.id}>
                    <TableCell>{person.id}</TableCell>
                    <TableCell>
                      <Link className="underline" to={`/persons/${person.id}`}>
                        {person.canonical_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{person.normalized_name}</TableCell>
                    <TableCell>{person.status}</TableCell>
                    <TableCell>
                      {person.merged_into_id ? (
                        <Link className="underline" to={`/persons/${person.merged_into_id}`}>
                          {person.merged_into_id}
                        </Link>
                      ) : (
                        DASH
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pager page={page} pageSize={PAGE_SIZE} shown={rows.length} onPage={(next) => url.set({ page: next === 1 ? null : next })} />
          </>
        )}
      </QueryState>
    </>
  );
}
