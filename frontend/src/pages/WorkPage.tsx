import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  getCycleV1,
  startCycleV1,
  stopCycleRunV1,
  type CycleResponse,
  type CycleTaskResponse,
  type EntityModelNoticeResponse
} from "@/api/generated";
import { ConfirmButton } from "@/components/ConfirmButton";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
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

function money(value: number | null | undefined): string {
  return value === null || value === undefined ? "неизвестна" : `$${value.toFixed(2)}`;
}

/** The entity model the round will ask, before «Запустить»: its id and price, or why the price is not known. */
export function EntityModelNotice({ notice, title = "Модель извлечения сущностей" }: { notice: EntityModelNoticeResponse; title?: string }) {
  const known = notice.prompt_usd_per_million !== null && notice.prompt_usd_per_million !== undefined;
  return (
    <div className="space-y-1 rounded-md border p-3 text-sm" aria-label={title}>
      <p className="font-medium">{title}</p>
      <p>
        Модель: <code>{notice.model}</code> {notice.free ? <strong>FREE</strong> : null}
      </p>
      {notice.free ? (
        <p>Стоимость: $0</p>
      ) : known ? (
        <>
          <p>Входные токены: {money(notice.prompt_usd_per_million)} / 1 млн</p>
          <p>Выходные токены: {money(notice.completion_usd_per_million)} / 1 млн</p>
        </>
      ) : (
        <p className="text-amber-700">Текущие цены неизвестны: каталог OpenRouter не ответил.</p>
      )}
      {notice.price_stale ? <p className="text-amber-700">{notice.price_text}</p> : null}
      <p>Тип: {notice.kind}</p>
    </div>
  );
}

const MARKS: Record<string, [string, string]> = {
  done: ["✓", "готово"],
  running: ["●", "выполняется"],
  ready: ["○", "ожидает запуска"],
  waiting: ["○", "ожидает"]
};

/** «с 01.10.2026 по настоящее время», «с 01.10.2026 по 10.10.2026». */
function periodText(from: string, to: string): string {
  return `с ${formatDate(from)} по ${to ? formatDate(to) : "настоящее время"}`;
}

/** What the latest load that ended well took: its dates, or everything new. */
function lastLoadText(cycle: CycleResponse): string | null {
  const last = cycle.last_load;
  if (!last) {
    return null;
  }
  const dates =
    last.published_from && last.published_to
      ? `за ${formatDate(last.published_from)}–${formatDate(last.published_to)}`
      : last.published_from
        ? `с ${formatDate(last.published_from)}`
        : "без дат";
  return `Последняя загрузка: ${formatDateTime(last.finished_at)} (${dates}).`;
}

/** The dates of «Сделать всё»: without them the round goes on from its step and its load takes
 * everything new; with a date «с» it is steps 1–5, the load taking that period. */
function Period({
  cycle,
  from,
  to,
  onFrom,
  onTo
}: {
  cycle: CycleResponse;
  from: string;
  to: string;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const last = lastLoadText(cycle);
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">
          Скачать с
          <input
            type="date"
            className="rounded-md border px-2 py-1"
            value={from}
            min={cycle.working_date ?? undefined}
            max={to || today}
            onChange={(event) => onFrom(event.target.value)}
          />
        </label>
        <label className="flex items-center gap-1">
          по
          <input
            type="date"
            className="rounded-md border px-2 py-1"
            value={to}
            min={from || cycle.working_date || undefined}
            max={today}
            onChange={(event) => onTo(event.target.value)}
          />
        </label>
        <span className="text-muted-foreground">пусто — по настоящее время</span>
      </div>
      <p className="text-muted-foreground">
        {from
          ? `Шаги 1–5: загрузить опубликованное ${periodText(from, to)} (мимо уже скачанного), затем обработать всю базу.`
          : "Без дат — догрузить всё новое с прошлой загрузки и обработать всю базу."}{" "}
        {last}
      </p>
      <p className="text-muted-foreground">
        {cycle.working_date ? `Рабочая дата ${formatDate(cycle.working_date)}: опубликованное раньше неё шаг 2 удаляет, поэтому «с» не раньше неё. ` : null}
        Telegram — не глубже 30 дней, Коммерсант — только свежая лента.
      </p>
    </div>
  );
}

function Steps({ cycle }: { cycle: CycleResponse }) {
  const client = useQueryClient();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const refresh = async () => {
    await readAgainAfterDecision(client);
  };
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        startCycleV1({
          body: {
            after: cycle.latest_run_id,
            entity_model: cycle.entity_model?.model ?? null,
            decision_model: cycle.decision_model?.model ?? null,
            // Only a period is sent: without dates the press is what it always was.
            ...(from ? { published_from: from } : {}),
            ...(from && to ? { published_to: to } : {})
          }
        })
      ),
    onSettled: refresh
  });
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
                question={from ? `Загрузить публикации ${periodText(from, to)}, затем обработать. ${cycle.period_question ?? ""}` : cycle.chain_question}
                details={
                  cycle.entity_model || cycle.decision_model ? (
                    <div className="space-y-2">
                      {cycle.entity_model ? <EntityModelNotice notice={cycle.entity_model} /> : null}
                      {cycle.decision_model ? <EntityModelNotice notice={cycle.decision_model} title="Модель решений (фигуранты, политичность)" /> : null}
                    </div>
                  ) : null
                }
                confirm="Запустить"
                disabled={start.isPending}
                onConfirm={() => start.mutate()}
              >
                Сделать всё
              </ConfirmButton>
              <span className="text-muted-foreground">
                {from ? "шаги 1–5 подряд" : cycle.chain_span}; по одному шагу — в{" "}
                <Link className="underline" to="/runs">
                  журнале запусков
                </Link>
              </span>
            </>
          )}
        </div>
        {cycle.live ? null : <Period cycle={cycle} from={from} to={to} onFrom={setFrom} onTo={setTo} />}
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
