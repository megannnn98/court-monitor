import { useQuery } from "@tanstack/react-query";

import { getOperationRunV1, listOperationRunsV1, type OperationRunResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { isEmptyList, QueryState } from "@/components/QueryState";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatDateTime, formatDuration } from "@/lib/format";
import { OPERATION_STATUS } from "@/lib/labels";

/** Polled while something runs, as the legacy `/ui/logs` does. */
export const POLL_MS = 5_000;
const LIVE = new Set(["pending", "running"]);

export function isLive(run: OperationRunResponse): boolean {
  return LIVE.has(run.status);
}

function Output({ title, text }: { title: string; text: string }) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-semibold">{title}</h3>
      <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">{text || DASH}</pre>
    </div>
  );
}

export function RunDetail({ runId }: { runId: number }) {
  const run = useQuery({
    queryKey: ["operations", "runs", runId],
    queryFn: () => unwrap(getOperationRunV1({ path: { run_id: runId } })),
    refetchInterval: (query) => (query.state.data && isLive(query.state.data) ? POLL_MS : false)
  });

  return (
    <QueryState query={run}>
      {(item) => (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-2">
              Запуск #{item.id}: {item.title} <StatusBadge status={item.status} labels={OPERATION_STATUS} />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>
              Создан {formatDateTime(item.created_at)}, начат {formatDateTime(item.started_at)}, закончен{" "}
              {formatDateTime(item.finished_at)}; длительность {formatDuration(item.duration_seconds)}; код возврата{" "}
              {item.return_code ?? DASH}.
            </p>
            <p className="font-mono text-xs break-all">{item.command.join(" ")}</p>
            {item.error ? <p className="text-destructive">{item.error}</p> : null}
            <Output title="stderr" text={item.stderr} />
            <Output title="stdout" text={item.stdout} />
          </CardContent>
        </Card>
      )}
    </QueryState>
  );
}

export function RunsPage() {
  const url = useUrlState();
  const selected = url.getNumber("run", 0);
  const runs = useQuery({
    queryKey: ["operations", "runs"],
    queryFn: () => unwrap(listOperationRunsV1()),
    refetchInterval: (query) => (query.state.data?.some(isLive) ? POLL_MS : false)
  });

  return (
    <>
      <PageHeader title="Журнал запусков" instruction="Запуски операций и их состояние. Здесь только просмотр: запуск и остановка — в старом журнале.">
        <p className="text-sm">
          <a className="underline" href="/ui/runs">
            Полный журнал в старом интерфейсе
          </a>
        </p>
      </PageHeader>
      <QueryState query={runs} isEmpty={isEmptyList} empty="Запусков ещё не было.">
        {(rows) => (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Запуск</TableHead>
                <TableHead>Операция</TableHead>
                <TableHead>Начат</TableHead>
                <TableHead>Длительность</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((run) => (
                <TableRow key={run.id} data-state={run.id === selected ? "selected" : undefined}>
                  <TableCell>#{run.id}</TableCell>
                  <TableCell>{run.title}</TableCell>
                  <TableCell>{formatDateTime(run.started_at ?? run.created_at)}</TableCell>
                  <TableCell>{formatDuration(run.duration_seconds)}</TableCell>
                  <TableCell>
                    <StatusBadge status={run.status} labels={OPERATION_STATUS} />
                  </TableCell>
                  <TableCell>
                    <Button variant="link" size="sm" onClick={() => url.set({ run: run.id })}>
                      Подробно
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </QueryState>
      {selected > 0 ? <RunDetail runId={selected} /> : null}
    </>
  );
}
