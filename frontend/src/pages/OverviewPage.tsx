import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getOverviewV1, type OverviewNewsResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { unwrap } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";

const BAND = "space-y-2 rounded-lg border bg-card p-4";

function News({ title, count, items, kind, empty }: { title: string; count: number; items: OverviewNewsResponse[]; kind: string; empty: string }) {
  return (
    <section aria-label={title} className={BAND}>
      <h2 className="font-medium">
        {title} <span className="ml-1 rounded-full bg-muted px-2 text-xs">{count}</span>
      </h2>
      {items.length ? (
        <ul className="space-y-1 text-sm">
          {items.map((item) => (
            <li key={item.key}>
              <span className="text-muted-foreground">{formatDate(item.published_at)}</span>{" "}
              <Link className="font-medium underline" to={dossierPath(item.key)}>
                {item.name}
              </Link>
              <div className="text-xs text-muted-foreground">{item.reason}</div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{empty}</p>
      )}
      {count ? (
        <Link className="text-sm underline" to={`/political?queue=${kind}`}>
          Все: {count} →
        </Link>
      ) : null}
    </section>
  );
}

export function OverviewPage() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => unwrap(getOverviewV1()) });
  return (
    <>
      <PageHeader title="Обзор" instruction="Что нового: политические дела со свежей новостью — новое дело или приговор, и фигуранты, которых публикации не называют." />
      <QueryState query={overview}>
        {(data) => {
          const decisions = data.pairs + data.unclear_roles + data.unclear_verdicts;
          return (
            <div className="space-y-4">
              {data.source_errors ? (
                <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  Источников с ошибками загрузки за неделю: {data.source_errors} —{" "}
                  <Link className="underline" to="/runs">
                    журнал запусков
                  </Link>
                  .
                </p>
              ) : null}
              <div className="grid gap-4 md:grid-cols-2">
                <News title="Новые дела" count={data.new_cases} items={data.latest_new_cases} kind="new_case" empty="Новых дел нет." />
                <News title="Приговоры" count={data.sentences} items={data.latest_sentences} kind="sentence" empty="Приговоров нет." />
              </div>
              <section aria-label="Неопознанные фигуранты" className={BAND}>
                <h2 className="font-medium">
                  Неопознанные фигуранты <span className="ml-1 rounded-full bg-muted px-2 text-xs">{data.unnamed}</span>
                </h2>
                <p className="text-sm text-muted-foreground">Публикация не называет человека («17-летний житель Тюмени»); кто это может быть — по перечню Росфинмониторинга.</p>
                {data.latest_unnamed.length ? (
                  <ul className="space-y-1 text-sm">
                    {data.latest_unnamed.map((item) => (
                      <li key={item.key}>
                        <span className="text-muted-foreground">{formatDate(item.published_at)}</span>{" "}
                        <Link className="underline" to={`/unnamed#u-${item.key}`}>
                          «{item.quote}»
                        </Link>
                        <div className="text-xs text-muted-foreground">
                          {item.facts} · {item.found}
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">Неопознанных нет.</p>
                )}
                {data.unnamed ? (
                  <Link className="text-sm underline" to="/unnamed">
                    Все: {data.unnamed} →
                  </Link>
                ) : null}
              </section>
              {decisions ? (
                <p className="text-sm">
                  Нужно ваше решение:{" "}
                  <Link className="underline" to="/review/pairs">
                    спорных совпадений — {data.pairs}
                  </Link>{" "}
                  ·{" "}
                  <Link className="underline" to="/review/roles">
                    неясных ролей — {data.unclear_roles}
                  </Link>{" "}
                  ·{" "}
                  <Link className="underline" to="/review/politics">
                    неясной политичности — {data.unclear_verdicts}
                  </Link>{" "}
                  ·{" "}
                  <Link className="underline" to="/work">
                    к работе
                  </Link>
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">Решений оператора не ждёт ничего.</p>
              )}
            </div>
          );
        }}
      </QueryState>
    </>
  );
}
