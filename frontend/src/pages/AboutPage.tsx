import { useQuery } from "@tanstack/react-query";

import { getAboutV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import { unwrap } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

const UNKNOWN = "неизвестно";

export function AboutPage() {
  const about = useQuery({ queryKey: ["about"], queryFn: () => unwrap(getAboutV1()) });

  return (
    <>
      <PageHeader title="О системе" instruction="Какой код и данные стоят за числами в консоли." />
      <QueryState query={about}>
        {(info) => (
          <>
            <h2 className="mb-2 text-lg font-semibold">Сборка</h2>
            <Table className="mb-4 max-w-xl">
              <TableBody>
                {(
                  [
                    ["Версия приложения", info.version],
                    ["Тег", info.tag],
                    ["Коммит", info.commit],
                    ["Собран", info.built_at],
                    ["Публикаций в базе", formatNumber(info.articles)],
                    ["Людей в базе", formatNumber(info.people)],
                    [
                      "Последний успешный запуск",
                      info.last_successful_run_at ? formatDateTime(info.last_successful_run_at) : UNKNOWN
                    ]
                  ] as const
                ).map(([name, value]) => (
                  <TableRow key={name}>
                    <TableCell className="font-medium">{name}</TableCell>
                    <TableCell>{value}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="mb-2 text-sm text-muted-foreground">
              Коммит и время сборки проставляются при сборке образа (<code>BUILD_COMMIT</code>, <code>BUILD_TIME</code>,{" "}
              <code>BUILD_TAG</code>). Тег вида «0.36.0-3-g495d9e9» значит: три коммита после тега 0.36.0. Запуск из
              рабочей копии без пересборки показывает «{info.commit || UNKNOWN}» — это значит, что страница говорит не о том
              коде, который вы правите.
            </p>
            <h2 className="mb-2 text-lg font-semibold">Что считается</h2>
            <p className="text-sm text-muted-foreground">
              Публикации и люди — те же счётчики, что в полосе показателей наверху каждой страницы старого интерфейса:{" "}
              <code>parsed_articles</code> и <code>entity_groups</code>. Проверяются на {formatDateTime(info.checked_at)}.
            </p>
            <p className="text-sm text-muted-foreground">Эта страница ничего не меняет и ни на что не влияет.</p>
          </>
        )}
      </QueryState>
    </>
  );
}
