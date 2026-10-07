import { useQuery } from "@tanstack/react-query";

import { listRosfinmonitoringEntriesV1, listRosfinmonitoringSnapshotsV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { Pager } from "@/components/Pager";
import { isEmptyList, QueryState } from "@/components/QueryState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, externalUrl, formatDate, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 100;

function Entries({ snapshotId }: { snapshotId: number }) {
  const url = useUrlState();
  const page = Math.max(1, url.getNumber("page", 1));
  const entries = useQuery({
    queryKey: ["rosfinmonitoring", "entries", snapshotId, page],
    queryFn: () =>
      unwrap(
        listRosfinmonitoringEntriesV1({
          path: { snapshot_id: snapshotId },
          query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }
        })
      )
  });
  return (
    <QueryState query={entries} isEmpty={(rows) => isEmptyList(rows) && page === 1} empty="В этом snapshot записей нет.">
      {(rows) => (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>№</TableHead>
                <TableHead>ФИО</TableHead>
                <TableHead>Дата рождения</TableHead>
                <TableHead>Основание</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((entry, index) => (
                <TableRow key={entry.id}>
                  <TableCell>{(page - 1) * PAGE_SIZE + index + 1}</TableCell>
                  <TableCell>{entry.full_name}</TableCell>
                  <TableCell>{formatDate(entry.birth_date)}</TableCell>
                  <TableCell className="whitespace-normal">{entry.inclusion_reason || DASH}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pager page={page} pageSize={PAGE_SIZE} shown={rows.length} onPage={(next) => url.set({ page: next === 1 ? null : next })} />
        </>
      )}
    </QueryState>
  );
}

export function RfmPage() {
  const url = useUrlState();
  const snapshots = useQuery({
    queryKey: ["rosfinmonitoring", "snapshots", 20],
    queryFn: () => unwrap(listRosfinmonitoringSnapshotsV1({ query: { limit: 20 } }))
  });
  const selected = url.getNumber("snapshot", snapshots.data?.[0]?.id ?? 0);

  return (
    <>
      <PageHeader title="Перечень РФМ" instruction="Загруженные снимки перечня Росфинмониторинга и записи выбранного снимка.">
        <p className="text-sm">
          <a className="underline" href="/ui/rfm/export.xlsx">
            Скачать перечень в Excel
          </a>{" "}
          · выбор периода — в{" "}
          <a className="underline" href="/ui/rfm">
            старом интерфейсе
          </a>
        </p>
      </PageHeader>
      <QueryState query={snapshots} isEmpty={isEmptyList} empty="Перечень ещё не загружен.">
        {(items) => (
          <Table className="mb-6">
            <TableHeader>
              <TableRow>
                <TableHead>Snapshot</TableHead>
                <TableHead>Дата</TableHead>
                <TableHead>Записей</TableHead>
                <TableHead>Источник</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const link = externalUrl(item.source_url);
                return (
                  <TableRow key={item.id} data-state={item.id === selected ? "selected" : undefined}>
                    <TableCell>
                      <button
                        type="button"
                        className={cn("underline", item.id === selected && "font-semibold")}
                        onClick={() => url.set({ snapshot: item.id, page: null })}
                      >
                        {item.id}
                      </button>
                    </TableCell>
                    <TableCell>{formatDate(item.snapshot_date)}</TableCell>
                    <TableCell>{formatNumber(item.entry_count)}</TableCell>
                    <TableCell className="break-all whitespace-normal">
                      {link ? (
                        <a className="underline" href={link} rel="noopener noreferrer" target="_blank">
                          {link}
                        </a>
                      ) : (
                        item.source_url
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </QueryState>
      {selected > 0 ? (
        <>
          <h2 className="mb-2 text-lg font-semibold">Записи snapshot {selected}</h2>
          <Entries snapshotId={selected} />
        </>
      ) : null}
    </>
  );
}
