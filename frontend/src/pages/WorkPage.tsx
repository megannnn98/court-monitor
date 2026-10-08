import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getCycleV1, startCycleV1, stopCycleRunV1, type CycleResponse, type CycleTaskResponse } from "@/api/generated";
import { ConfirmButton } from "@/components/ConfirmButton";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { unwrap } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Polled while a step runs, as the legacy page reloads every 5 s. */
export const POLL_MS = 5_000;

// The review stations already in the new console; the others open the legacy page.
const MOVED: Record<string, string> = {
  "/ui/pairs": "/review/pairs",
  "/ui/roles": "/review/roles",
  "/ui/politics-review": "/review/politics",
  "/ui/unnamed": "/unnamed",
  "/ui/base-unnamed": "/base-unnamed",
  "/ui/junk-holds": "/junk-holds"
};

function TaskLink({ task, children, className }: { task: CycleTaskResponse; children: ReactNode; className?: string }) {
  const path = MOVED[task.href];
  return path ? (
    <Link className={className} to={path}>
      {children}
    </Link>
  ) : (
    <a className={className} href={task.href}>
      {children}
    </a>
  );
}

const MARKS: Record<string, [string, string]> = {
  done: ["✓", "готово"],
  running: ["●", "выполняется"],
  ready: ["○", "ожидает запуска"],
  waiting: ["○", "ожидает"]
};

function Steps({ cycle }: { cycle: CycleResponse }) {
  const client = useQueryClient();
  const refresh = async () => {
    await Promise.all([client.invalidateQueries({ queryKey: ["cycle"] }), client.invalidateQueries({ queryKey: ["status"] })]);
  };
  const start = useMutation({ mutationFn: () => unwrap(startCycleV1({ body: { after: cycle.latest_run_id } })), onSettled: refresh });
  const stop = useMutation({
    mutationFn: (runId: number) => unwrap(stopCycleRunV1({ path: { run_id: runId } })),
    onSettled: refresh
  });
  const failure = start.error ?? stop.error;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Обработка данных</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">Автоматические шаги. Ручная проверка показана выше.</p>
        <ol className="space-y-1">
          {cycle.steps.map((step) => {
            const [mark, words] = MARKS[step.status] ?? ["○", step.status];
            return (
              <li key={step.stage} className={cn("flex gap-2", step.status === "running" && "font-medium", step.status === "done" && "text-muted-foreground")}>
                <span aria-hidden="true">{mark}</span>
                {step.label}
                <small className="text-muted-foreground">{words}</small>
              </li>
            );
          })}
        </ol>
        {cycle.chain_note ? <p className="text-muted-foreground">{cycle.chain_note}</p> : null}
        {cycle.chain_stopped ? <p className="text-amber-700">{cycle.chain_stopped}</p> : null}
        <div className="flex flex-wrap items-center gap-3">
          {cycle.live ? (
            <>
              <ConfirmButton
                variant="destructive"
                title="Остановить запуск?"
                question="Уже сделанное останется."
                confirm="Остановить"
                disabled={stop.isPending}
                onConfirm={() => stop.mutate(cycle.live!.run_id)}
              >
                Остановить
              </ConfirmButton>
              <Link className="underline" to={`/logs?run_id=${cycle.live.run_id}`}>
                Ход запуска #{cycle.live.run_id}: {cycle.live.title}
              </Link>
            </>
          ) : (
            <>
              <ConfirmButton
                title="Сделать всё?"
                question={cycle.chain_question}
                confirm="Запустить"
                disabled={start.isPending}
                onConfirm={() => start.mutate()}
              >
                Сделать всё
              </ConfirmButton>
              <span className="text-muted-foreground">
                {cycle.chain_span}; по одному шагу — в{" "}
                <Link className="underline" to="/runs">
                  журнале запусков
                </Link>
              </span>
            </>
          )}
        </div>
        {failure ? <p className="text-destructive">{failure.message}</p> : null}
      </CardContent>
    </Card>
  );
}

export function WorkPage() {
  const cycle = useQuery({
    queryKey: ["cycle"],
    queryFn: () => unwrap(getCycleV1()),
    refetchInterval: (query) => (query.state.data?.live ? POLL_MS : false)
  });

  return (
    <>
      <PageHeader title="Работа" instruction="Сначала выполните одну показанную проверку; состояние автоматической обработки ниже." />
      <QueryState query={cycle}>
        {(data) => (
          <div className="space-y-4">
            <Card>
              <CardContent className="space-y-2">
                {data.attention ? (
                  <>
                    <p className="text-xs uppercase text-muted-foreground">Требует вашего внимания</p>
                    <h2 className="text-lg font-semibold">
                      {data.attention.title}: {formatNumber(data.attention.count)}
                    </h2>
                    <p className="text-sm">{data.attention.description}</p>
                    <TaskLink task={data.attention} className="inline-block rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground">
                      Начать проверку
                    </TaskLink>
                  </>
                ) : data.live ? (
                  <>
                    <p className="text-xs uppercase text-muted-foreground">Состояние системы</p>
                    <h2 className="text-lg font-semibold">Идёт автоматическая обработка</h2>
                    <p className="text-sm">Результаты появятся после завершения текущего шага.</p>
                  </>
                ) : (
                  <>
                    <p className="text-xs uppercase text-muted-foreground">Требует вашего внимания</p>
                    <h2 className="text-lg font-semibold">Сейчас ничего проверять не нужно</h2>
                    <p className="text-sm">Автоматическая обработка завершена или ожидает следующего запуска.</p>
                  </>
                )}
              </CardContent>
            </Card>
            {data.tasks.length ? (
              <Card>
                <CardHeader>
                  <CardTitle>Далее</CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 text-sm">
                  {data.tasks.map((task) => (
                    <TaskLink key={task.key} task={task} className="flex justify-between underline">
                      <span>{task.title}</span>
                      <strong>{formatNumber(task.count)}</strong>
                    </TaskLink>
                  ))}
                </CardContent>
              </Card>
            ) : null}
            <Steps cycle={data} />
          </div>
        )}
      </QueryState>
    </>
  );
}
