import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { candidateTableV1, listRosfinmonitoringSnapshotsV1, type CandidateTableResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatConfidence, formatDate, formatNumber } from "@/lib/format";

const DEFAULT_CONFIDENCE = 0.7;
const DEFAULT_LIMIT = 100;
// «Новости за всё время» is the server's empty date_from: an empty value leaves the
// address, so it travels as this.
const EVERY_DAY = "all";

type Query = {
  snapshot_id?: number;
  min_confidence: number;
  limit: number;
  date_from?: string;
  include_administrative: boolean;
  criminal_only: boolean;
  event_date_filter: boolean;
};

/** The legacy Excel file of exactly the table's selection: the snapshot and the period
 * the server chose, the same switches (`web.candidate_rows.candidate_filters`). */
export function exportHref(query: Query, table: CandidateTableResponse): string {
  const params = new URLSearchParams({
    snapshot_id: String(table.snapshot_id),
    min_confidence: String(query.min_confidence),
    date_from: table.period_start ?? ""
  });
  if (query.include_administrative) {
    params.set("include_administrative", "1");
  }
  if (query.criminal_only) {
    params.set("criminal_only", "1");
  }
  if (!query.event_date_filter) {
    params.set("event_date_filter", "0");
  }
  return `/ui/candidates/export.xlsx?${params.toString()}`;
}

export function CandidatesPage() {
  const url = useUrlState();
  const snapshot = url.getNumber("snapshot", 0);
  const dateFrom = url.get("date_from");
  const query: Query = {
    ...(snapshot > 0 ? { snapshot_id: snapshot } : {}),
    min_confidence: url.getNumber("min_confidence", DEFAULT_CONFIDENCE),
    limit: url.getNumber("limit", DEFAULT_LIMIT),
    // Absent: the server's default period; «all»: every day.
    ...(dateFrom ? { date_from: dateFrom === EVERY_DAY ? "" : dateFrom } : {}),
    include_administrative: url.get("include_administrative") === "1",
    criminal_only: url.get("criminal_only") === "1",
    event_date_filter: url.get("event_date_filter") !== "0"
  };

  const snapshots = useQuery({
    queryKey: ["rosfinmonitoring", "snapshots", 20],
    queryFn: () => unwrap(listRosfinmonitoringSnapshotsV1({ query: { limit: 20 } }))
  });
  const table = useQuery({
    queryKey: ["candidates", "table", query],
    queryFn: () => unwrap(candidateTableV1({ query })),
    placeholderData: keepPreviousData
  });

  const [draft, setDraft] = useState({ minConfidence: String(query.min_confidence), limit: String(query.limit) });
  useEffect(
    () => setDraft({ minConfidence: String(query.min_confidence), limit: String(query.limit) }),
    [query.min_confidence, query.limit]
  );

  function apply(event: FormEvent) {
    event.preventDefault();
    url.set({
      min_confidence: Number(draft.minConfidence) === DEFAULT_CONFIDENCE ? null : draft.minConfidence,
      limit: Number(draft.limit) === DEFAULT_LIMIT ? null : draft.limit
    });
  }

  const switches: [string, string, boolean, (on: boolean) => void][] = [
    ["Включая административные", "include-administrative", query.include_administrative, (on) => url.set({ include_administrative: on ? "1" : null })],
    ["Только уголовные (УК)", "criminal-only", query.criminal_only, (on) => url.set({ criminal_only: on ? "1" : null })],
    ["Фильтр по дате события", "event-date-filter", query.event_date_filter, (on) => url.set({ event_date_filter: on ? null : "0" })]
  ];

  return (
    <>
      <PageHeader
        title="Кандидаты"
        instruction="Кандидаты — политически классифицированные люди с подтверждённым статусом РФМ not_matched."
      />
      <form onSubmit={apply} className="mb-3 flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label>Snapshot РФМ</Label>
          <Select value={String(table.data?.snapshot_id ?? snapshot)} onValueChange={(value) => url.set({ snapshot: value })}>
            <SelectTrigger className="w-72" aria-label="Snapshot РФМ">
              <SelectValue placeholder="Последний" />
            </SelectTrigger>
            <SelectContent>
              {(snapshots.data ?? []).map((item) => (
                <SelectItem key={item.id} value={String(item.id)}>
                  {item.id} — {formatDate(item.snapshot_date)} ({formatNumber(item.entry_count)})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="min-confidence">Min confidence</Label>
          <Input
            id="min-confidence"
            type="number"
            min={0}
            max={1}
            step={0.05}
            className="w-28"
            value={draft.minConfidence}
            onChange={(event) => setDraft({ ...draft, minConfidence: event.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="limit">Limit</Label>
          <Input
            id="limit"
            type="number"
            min={1}
            max={1000}
            className="w-28"
            value={draft.limit}
            onChange={(event) => setDraft({ ...draft, limit: event.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="date-from">Новости с</Label>
          <Input
            id="date-from"
            type="date"
            className="w-40"
            value={dateFrom && dateFrom !== EVERY_DAY ? dateFrom : (table.data?.period_start ?? "")}
            onChange={(event) => url.set({ date_from: event.target.value || EVERY_DAY })}
          />
        </div>
        <Button type="submit">Обновить</Button>
      </form>
      <div className="mb-3 flex flex-wrap gap-4 text-sm">
        {switches.map(([label, id, on, set]) => (
          <label key={id} className="flex items-center gap-2">
            <Checkbox id={id} checked={on} onCheckedChange={(checked) => set(checked === true)} />
            {label}
          </label>
        ))}
        <button type="button" className="underline" onClick={() => url.set({ date_from: EVERY_DAY })}>
          Новости за всё время
        </button>
      </div>
      <QueryState query={table}>
        {(data) => (
          <>
            <p className="mb-2 text-sm text-muted-foreground">
              Найдено: {formatNumber(data.total)}, показано: {formatNumber(data.items.length)}.{" "}
              {data.period_start ? `Новости с ${formatDate(data.period_start)}.` : "Новости за всё время."} Сортировка по
              категории события и дате новости.{" "}
              <a className="underline" href={exportHref(query, data)}>
                Скачать Excel
              </a>{" "}
              — те же люди, без ограничения Limit.
            </p>
            {data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">Кандидатов при этих условиях нет.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>№</TableHead>
                    <TableHead>Person ID</TableHead>
                    <TableHead>Персона</TableHead>
                    <TableHead>Дата новости</TableHead>
                    <TableHead>Категория</TableHead>
                    <TableHead>Political confidence</TableHead>
                    <TableHead>Events</TableHead>
                    <TableHead>RF status</TableHead>
                    <TableHead>Причины</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row, index) => (
                    <TableRow key={row.person_id}>
                      <TableCell>{index + 1}</TableCell>
                      <TableCell>
                        <Link className="underline" to={`/persons/${row.person_id}`}>
                          {row.person_id}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Link className="underline" to={`/persons/${row.person_id}`}>
                          {row.name}
                        </Link>
                      </TableCell>
                      <TableCell>{formatDate(row.news_day)}</TableCell>
                      <TableCell>{row.category ?? DASH}</TableCell>
                      <TableCell>{formatConfidence(row.persecution_confidence)}</TableCell>
                      <TableCell>{formatNumber(row.event_count)}</TableCell>
                      <TableCell>{row.rosfinmonitoring_status}</TableCell>
                      <TableCell className="whitespace-normal">{row.reasons.join(", ")}</TableCell>
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
