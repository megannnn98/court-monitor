import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { getAirtableV1, refreshRosfinV1, syncReferenceListsV1, type AirtableSyncResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { unwrap } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

const SECTION = "space-y-3 rounded-lg border bg-card p-4";

/** What the last sync did, list by list; a failed list is said, not hidden. */
function SyncReport({ report, labels }: { report: AirtableSyncResponse; labels: Record<string, string> }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">
        Последняя синхронизация: {formatDateTime(report.started_at)}
        {report.mode === "files" ? " (из файлов)" : ""}
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            {["Справочник", "Создано", "Обновлено", "Без изменений", "Статус"].map((name) => (
              <TableHead key={name}>{name}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(report.tables).map(([name, item]) => {
            const failed = item.status === "error" || Boolean(item.error);
            return (
              <TableRow key={name}>
                <TableCell>{labels[name] ?? name}</TableCell>
                <TableCell>{item.created ?? 0}</TableCell>
                <TableCell>{item.updated ?? 0}</TableCell>
                <TableCell>{item.unchanged ?? 0}</TableCell>
                <TableCell className={failed ? "text-destructive" : item.status === "skipped" ? "text-muted-foreground" : undefined}>
                  {failed ? `ошибка: ${item.error ?? ""}` : item.status === "skipped" ? `пропущен${item.error ? `: ${item.error}` : ""}` : "готово"}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

export function AirtablePage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const overview = useQuery({ queryKey: ["airtable"], queryFn: () => unwrap(getAirtableV1()) });
  const sync = useMutation({
    mutationFn: () => unwrap(syncReferenceListsV1()),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["airtable"] });
    }
  });
  const refresh = useMutation({
    mutationFn: () => unwrap(refreshRosfinV1()),
    // The download and the check run in the background: their card is in the journal.
    onSuccess: (started) => navigate(`/runs?run=${started.run_id}`)
  });

  return (
    <>
      <PageHeader
        title="База Airtable"
        instruction="Источники, найденные люди, должностные лица и статьи ведутся в Airtable. Кнопка переносит их в PostgreSQL; дальше система работает только с базой."
      />
      <QueryState query={overview}>
        {(data) => {
          const labels = Object.fromEntries(data.lists.map((item) => [item.name, item.label]));
          return (
            <div className="space-y-4">
              {data.configured ? null : <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">Синхронизация недоступна: {data.reason}</p>}
              {data.mode_note ? <p className="text-sm text-muted-foreground">{data.mode_note}</p> : null}
              {data.missing.length ? <p className="text-sm text-muted-foreground">Файла нет — список останется как был: {data.missing.join(", ")}.</p> : null}

              <section aria-label="Синхронизация справочников" className={SECTION}>
                <h2 className="font-medium">Синхронизация справочников</h2>
                <p className="text-sm text-muted-foreground">Airtable — внешняя админка, PostgreSQL — рабочее хранилище. Кнопка переносит четыре справочника в базу; обработка статей к Airtable не обращается.</p>
                <Button disabled={!data.configured || sync.isPending} onClick={() => sync.mutate()}>
                  {sync.isPending ? "Синхронизирую…" : "Синхронизировать Airtable"}
                </Button>
                {sync.isError ? <p className="text-sm text-destructive">{sync.error.message}</p> : null}
                {sync.data ? <SyncReport report={sync.data} labels={labels} /> : <p className="text-sm text-muted-foreground">Синхронизация ещё не выполнялась.</p>}
              </section>

              <section aria-label="Официальный перечень РФМ" className={SECTION}>
                <h2 className="font-medium">Официальный перечень РФМ (fedsfm.ru)</h2>
                {data.official ? (
                  <p className="text-sm text-muted-foreground">
                    Снимок #{data.official.snapshot_id} от {formatDateTime(data.official.snapshot_date)}, записей {formatNumber(data.official.entry_count)}, сверено{" "}
                    {formatNumber(data.official.match_count)}.
                  </p>
                ) : (
                  <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">Перечень ещё не загружен: сверить людей с ним не с чем.</p>
                )}
                <p className="text-sm">
                  Кнопка скачивает опубликованный список, <strong>создаёт новый снимок только если перечень изменился</strong>, и пересверяет людей с перечнем заново.
                </p>
                <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
                  Обновить перечень и сверить с РФМ
                </Button>
                {refresh.isError ? <p className="text-sm text-destructive">{refresh.error.message}</p> : null}
                <p className="text-sm text-muted-foreground">
                  Скачивание и сверка идут в фоне: после нажатия откроется карточка запуска в{" "}
                  <Link className="underline" to="/runs">
                    журнале запусков
                  </Link>
                  .
                </p>
              </section>

              <section aria-label="Что сейчас в базе" className={SECTION}>
                <h2 className="font-medium">Что сейчас в базе</h2>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Справочник</TableHead>
                      <TableHead className="text-right">Записей</TableHead>
                      <TableHead>{data.source_column}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.lists.map((item) => (
                      <TableRow key={item.name}>
                        <TableCell>
                          {/* The officials are edited here, not read from Airtable. */}
                          {item.name === "officials" ? (
                            <Link className="underline" to="/officials">
                              {item.label}
                            </Link>
                          ) : (
                            item.label
                          )}
                        </TableCell>
                        <TableCell className="text-right">{formatNumber(item.count)}</TableCell>
                        <TableCell className="break-all whitespace-normal text-muted-foreground">{item.source || "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>
            </div>
          );
        }}
      </QueryState>
    </>
  );
}
