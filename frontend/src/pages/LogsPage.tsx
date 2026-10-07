import { useQuery } from "@tanstack/react-query";

import { listOperationRunsV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { isEmptyList, QueryState } from "@/components/QueryState";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { OPERATION_STATUS } from "@/lib/labels";
import { isLive, POLL_MS, RunDetail } from "@/pages/RunsPage";

/** As many as the legacy «Логи» lists. */
export const RUNS_LISTED = 20;

export function LogsPage() {
  const url = useUrlState();
  const runs = useQuery({
    queryKey: ["operations", "runs"],
    queryFn: () => unwrap(listOperationRunsV1()),
    refetchInterval: (query) => (query.state.data?.some(isLive) ? POLL_MS : false)
  });

  return (
    <>
      <PageHeader title="Логи" instruction="Вывод запусков загрузки: журнал и результат, пока запуск идёт и после него.">
        <p className="text-sm text-muted-foreground">
          Остановка запуска и ход по источникам — в{" "}
          <a className="underline" href="/ui/logs">
            старом интерфейсе
          </a>
          . Хранятся последние символы вывода, начало длинного лога обрезается.
        </p>
      </PageHeader>
      <QueryState query={runs} isEmpty={isEmptyList} empty="Запусков ещё не было.">
        {(rows) => {
          // The latest run unless another one is chosen, as on the legacy page.
          const selected = url.getNumber("run", rows[0].id);
          return (
            <>
              <RunDetail runId={selected} />
              <h2 className="mt-6 mb-2 text-lg font-semibold">Запуски</h2>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Запуск</TableHead>
                    <TableHead>Операция</TableHead>
                    <TableHead>Начат</TableHead>
                    <TableHead>Статус</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.slice(0, RUNS_LISTED).map((run) => (
                    <TableRow key={run.id} data-state={run.id === selected ? "selected" : undefined}>
                      <TableCell>
                        <Button variant="link" size="sm" className="px-0" onClick={() => url.set({ run: run.id })}>
                          #{run.id}
                        </Button>
                      </TableCell>
                      <TableCell>{run.title}</TableCell>
                      <TableCell>{formatDateTime(run.created_at)}</TableCell>
                      <TableCell>
                        <StatusBadge status={run.status} labels={OPERATION_STATUS} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          );
        }}
      </QueryState>
    </>
  );
}
