import { Link } from "react-router-dom";

import { useStatus } from "@/hooks/useStatus";
import { formatNumber } from "@/lib/format";
import { labelOf, MONITORING_RUN_STATUS } from "@/lib/labels";
import { cn } from "@/lib/utils";

/** Above every page, as in the legacy UI. Nothing while loading; the error in one line. */
export function StatusStrip() {
  const status = useStatus();
  if (status.isPending) {
    return null;
  }
  if (status.isError) {
    return <p className="mb-4 text-xs text-destructive">Показатели не загрузились: {status.error.message}</p>;
  }
  const { articles, people, result, queue, latest_monitoring_status: latest, live_operation: live, balance } = status.data;
  const items: [string, string][] = [
    ["Публикации", formatNumber(articles)],
    ["Люди", formatNumber(people)],
    ["Результат", formatNumber(result)],
    ["Очередь", formatNumber(queue.total)],
    ["Последний запуск", latest ? labelOf(MONITORING_RUN_STATUS, latest) : "нет"]
  ];
  return (
    <section aria-label="Показатели" className="mb-4 space-y-2">
      {live ? (
        <Link to="/work" className="flex items-center gap-2 rounded-md bg-amber-100 px-3 py-1.5 text-sm text-amber-900">
          <span className="size-2 animate-pulse rounded-full bg-amber-500" aria-hidden="true" />
          Идёт: {live.title}
          <span className="ml-auto">ход →</span>
        </Link>
      ) : null}
      <dl className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        {items.map(([name, value]) => (
          <div key={name} className="flex items-baseline gap-1.5">
            <dt className="text-xs text-muted-foreground">{name}</dt>
            <dd className="font-semibold">{value}</dd>
          </div>
        ))}
        {/* The one figure that costs money when it runs out: a chip at the end of the line. */}
        {balance ? (
          <div
            title={balance.hint}
            className={cn(
              "ml-auto flex items-baseline gap-1.5 rounded-full border bg-card px-3 py-0.5",
              balance.low && "border-destructive text-destructive"
            )}
          >
            <dt className="text-xs">Баланс OpenRouter</dt>
            <dd className="font-semibold">{balance.figure}</dd>
          </div>
        ) : null}
      </dl>
    </section>
  );
}
