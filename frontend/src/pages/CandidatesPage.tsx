import { useEffect, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listCandidatesV1, listRosfinmonitoringSnapshotsV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { isEmptyList, QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { formatConfidence, formatDate, formatNumber } from "@/lib/format";

const DEFAULT_CONFIDENCE = 0.7;
const DEFAULT_LIMIT = 100;

export function CandidatesPage() {
  const url = useUrlState();
  const snapshots = useQuery({
    queryKey: ["rosfinmonitoring", "snapshots", 20],
    queryFn: () => unwrap(listRosfinmonitoringSnapshotsV1({ query: { limit: 20 } }))
  });
  // The newest snapshot unless another one is chosen, as on the legacy page.
  const snapshotId = url.getNumber("snapshot", snapshots.data?.[0]?.id ?? 0);
  const minConfidence = url.getNumber("min_confidence", DEFAULT_CONFIDENCE);
  const limit = url.getNumber("limit", DEFAULT_LIMIT);

  const [draft, setDraft] = useState({ minConfidence: String(minConfidence), limit: String(limit) });
  useEffect(() => setDraft({ minConfidence: String(minConfidence), limit: String(limit) }), [minConfidence, limit]);

  const candidates = useQuery({
    queryKey: ["candidates", snapshotId, minConfidence, limit],
    queryFn: () =>
      unwrap(listCandidatesV1({ query: { snapshot_id: snapshotId, min_persecution_confidence: minConfidence, limit } })),
    enabled: snapshotId > 0
  });

  function apply(event: FormEvent) {
    event.preventDefault();
    url.set({
      min_confidence: Number(draft.minConfidence) === DEFAULT_CONFIDENCE ? null : draft.minConfidence,
      limit: Number(draft.limit) === DEFAULT_LIMIT ? null : draft.limit
    });
  }

  return (
    <>
      <PageHeader
        title="Кандидаты"
        instruction="Кандидаты — политически классифицированные люди с подтверждённым статусом РФМ not_matched."
      />
      <QueryState
        query={snapshots}
        isEmpty={isEmptyList}
        empty="Snapshot Росфинмониторинга ещё не загружен: без него нельзя отличить подтверждённое отсутствие от отсутствия проверки."
      >
        {(items) => (
          <form onSubmit={apply} className="mb-4 flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label>Snapshot РФМ</Label>
              <Select value={String(snapshotId)} onValueChange={(value) => url.set({ snapshot: value })}>
                <SelectTrigger className="w-72" aria-label="Snapshot РФМ">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {items.map((item) => (
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
            <Button type="submit">Обновить</Button>
          </form>
        )}
      </QueryState>
      {snapshotId > 0 ? (
        <QueryState query={candidates} isEmpty={isEmptyList} empty="Кандидатов при этих условиях нет.">
          {(rows) => (
            <>
              <p className="mb-2 text-sm text-muted-foreground">Показано: {formatNumber(rows.length)}.</p>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>№</TableHead>
                    <TableHead>Person ID</TableHead>
                    <TableHead>Персона</TableHead>
                    <TableHead>Political confidence</TableHead>
                    <TableHead>Events</TableHead>
                    <TableHead>Алиасы</TableHead>
                    <TableHead>RF status</TableHead>
                    <TableHead>Причины</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row, index) => (
                    <TableRow key={row.person_id}>
                      <TableCell>{index + 1}</TableCell>
                      <TableCell>
                        <Link className="underline" to={`/persons/${row.person_id}`}>
                          {row.person_id}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Link className="underline" to={`/persons/${row.person_id}`}>
                          {row.canonical_name}
                        </Link>
                      </TableCell>
                      <TableCell>{formatConfidence(row.persecution_confidence)}</TableCell>
                      <TableCell>{formatNumber(row.event_count)}</TableCell>
                      <TableCell>{formatNumber(row.alias_count)}</TableCell>
                      <TableCell>{row.rosfinmonitoring_status}</TableCell>
                      <TableCell className="whitespace-normal">{row.persecution_reasons.join(", ")}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          )}
        </QueryState>
      ) : null}
    </>
  );
}
