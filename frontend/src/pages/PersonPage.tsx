import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getPersonDetailV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { unwrap } from "@/lib/api";
import { DASH, formatConfidence, formatDate } from "@/lib/format";

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-muted-foreground">{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PersonPage() {
  const personId = Number(useParams().personId);
  const detail = useQuery({
    queryKey: ["persons", personId, "detail"],
    queryFn: () => unwrap(getPersonDetailV1({ path: { person_id: personId } }))
  });

  return (
    <QueryState query={detail}>
      {({ person, aliases, persecution, rosfinmonitoring, events }) => (
        <>
          <PageHeader
            title={person.canonical_name}
            instruction="Карточка Person показывает только проверяемые факты с переходом к source span."
          />
          <div className="mb-6 grid gap-4 md:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Персона</CardTitle>
              </CardHeader>
              <CardContent>
                <Facts
                  rows={[
                    ["ID", person.id],
                    ["Статус", person.status],
                    ["Нормализованное", person.normalized_name],
                    ["Слит в", person.merged_into_id ? <Link className="underline" to={`/persons/${person.merged_into_id}`}>{person.merged_into_id}</Link> : DASH]
                  ]}
                />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Persecution</CardTitle>
              </CardHeader>
              <CardContent>
                {persecution ? (
                  <Facts
                    rows={[
                      ["Статус", persecution.status],
                      ["Confidence", formatConfidence(persecution.confidence)],
                      ["Причины", persecution.reasons.join(", ") || DASH],
                      ["Доказательства", persecution.evidence_types.join(", ") || DASH],
                      ["Классификатор", `${persecution.classifier_name} ${persecution.classifier_version}`]
                    ]}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">{DASH}</p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Росфинмониторинг</CardTitle>
              </CardHeader>
              <CardContent>
                {rosfinmonitoring ? (
                  <Facts
                    rows={[
                      ["Статус", rosfinmonitoring.status],
                      ["Confidence", formatConfidence(rosfinmonitoring.confidence)],
                      ["Запись", rosfinmonitoring.matched_entry_name ?? DASH],
                      // The list's page shows the latest snapshot only: no link that would pass it for this one.
                      ["Snapshot", rosfinmonitoring.snapshot_id],
                      ["Причины", rosfinmonitoring.reasons.join(", ") || DASH]
                    ]}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">{DASH}</p>
                )}
              </CardContent>
            </Card>
          </div>

          <h2 className="mb-2 text-lg font-semibold">Алиасы</h2>
          {aliases.length ? (
            <ul className="mb-6 list-disc pl-5 text-sm">
              {aliases.map((alias) => (
                <li key={alias.id}>
                  {alias.surface_text} <span className="text-muted-foreground">{alias.origin}, {formatConfidence(alias.confidence)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mb-6 text-sm text-muted-foreground">Алиасов нет.</p>
          )}

          <h2 className="mb-2 text-lg font-semibold">События</h2>
          {events.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>Тип</TableHead>
                  <TableHead>Дата</TableHead>
                  <TableHead>Роль</TableHead>
                  <TableHead>Статья</TableHead>
                  <TableHead>Span</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell>{event.id}</TableCell>
                    <TableCell>{event.event_type}</TableCell>
                    <TableCell>{formatDate(event.event_date)}</TableCell>
                    <TableCell>{event.role}</TableCell>
                    <TableCell className="whitespace-normal">
                      <Link
                        className="underline"
                        to={`/articles/${event.evidence.article_id}?start=${event.evidence.start_offset}&end=${event.evidence.end_offset}`}
                      >
                        {event.evidence.title}
                      </Link>
                    </TableCell>
                    <TableCell className="whitespace-normal">{event.evidence.text}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">Событий нет.</p>
          )}
        </>
      )}
    </QueryState>
  );
}
