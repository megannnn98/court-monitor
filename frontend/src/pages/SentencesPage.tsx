import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listSentencesV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { OptionSelect } from "@/components/OptionSelect";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatNumber } from "@/lib/format";

const HIDDEN = "hidden";
const ALL_REGIONS = "__all__";
// «Все дела» is the reason "": an empty value leaves the address, so it travels as this.
const ANY_REASON = "any";

export function SentencesPage() {
  const url = useUrlState();
  const query = {
    reason: url.get("reason", "political") === ANY_REASON ? "" : url.get("reason", "political"),
    region: url.get("region"),
    view: url.get("view"),
    page: Math.max(1, url.getNumber("page", 1))
  };
  const sentences = useQuery({
    queryKey: ["sentences", query],
    queryFn: () => unwrap(listSentencesV1({ query })),
    placeholderData: keepPreviousData
  });
  const hidden = query.view === HIDDEN;

  return (
    <>
      <PageHeader
        title="Приговоры"
        instruction="Приговоры, которые модель выписала из публикаций: регион суда, наказание и причина преследования, каждое — со словами самой публикации."
      >
        <p className="text-sm text-muted-foreground">
          «Неверно» и «Вернуть» пока в{" "}
          <a className="underline" href="/ui/sentences">
            старом интерфейсе
          </a>
          .
        </p>
      </PageHeader>
      <QueryState query={sentences}>
        {(data) => (
          <>
            <p className="mb-3 text-sm text-muted-foreground">
              Модель прочла публикаций с приговором: {formatNumber(data.read)}. Из них выписано дел: {formatNumber(data.cases)}. Один
              приговор, о котором написали несколько источников, — одно дело.
            </p>
            <div className="mb-3 flex flex-wrap gap-2">
              <Button size="sm" variant={hidden ? "outline" : "default"} onClick={() => url.set({ view: null, page: null })}>
                Приговоры
              </Button>
              <Button size="sm" variant={hidden ? "default" : "outline"} onClick={() => url.set({ view: HIDDEN, page: null })}>
                Убранные: {formatNumber(data.hidden)}
              </Button>
            </div>
            {hidden ? null : (
              <div className="mb-3 flex flex-wrap items-end gap-3">
                <OptionSelect
                  label="Причина преследования"
                  value={data.reason === "" ? ANY_REASON : data.reason}
                  options={data.reasons.map((option) => ({ ...option, value: option.value === "" ? ANY_REASON : option.value }))}
                  className="w-64"
                  onChange={(value) => url.set({ reason: value === "political" ? null : value, page: null })}
                />
                <OptionSelect
                  label="Регион"
                  value={data.region || ALL_REGIONS}
                  options={[{ value: ALL_REGIONS, label: "Все регионы" }, ...data.regions.map((name) => ({ value: name, label: name }))]}
                  className="w-64"
                  onChange={(value) => url.set({ region: value === ALL_REGIONS ? null : value, page: null })}
                />
              </div>
            )}
            <p className="mb-2 text-sm text-muted-foreground">Найдено: {formatNumber(data.total)}.</p>
            {data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">{hidden ? "Ничего не убрано." : "Таких приговоров нет."}</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Человек</TableHead>
                    <TableHead>Регион</TableHead>
                    <TableHead>Наказание</TableHead>
                    <TableHead>Срок</TableHead>
                    <TableHead>Дата</TableHead>
                    <TableHead>За что</TableHead>
                    <TableHead>Публикация и её слова</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row) => (
                    <TableRow key={row.row_ids.join(",")}>
                      <TableCell className="whitespace-normal">{row.person}</TableCell>
                      <TableCell>{row.region ?? DASH}</TableCell>
                      <TableCell>
                        {row.kind_label}
                        {row.in_absentia ? " (заочно)" : ""}
                      </TableCell>
                      <TableCell>{row.term}</TableCell>
                      <TableCell>{row.sentenced_on ?? DASH}</TableCell>
                      <TableCell className="whitespace-normal">
                        {row.reason_label}
                        <div className="text-xs text-muted-foreground">{row.reason_text}</div>
                      </TableCell>
                      <TableCell className="min-w-64 whitespace-normal">
                        <Link className="underline" to={`/articles/${row.article_id}`}>
                          {row.title}
                        </Link>
                        {row.source ? (
                          <div className="text-xs text-muted-foreground">
                            {row.source}
                            {row.more_publications ? ` и ещё ${row.more_publications}` : ""}
                          </div>
                        ) : null}
                        <div className="text-sm">«{row.quote}»</div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {hidden ? null : (
              <Pager
                page={query.page}
                pageSize={data.page_size}
                shown={data.items.length}
                total={data.total}
                onPage={(next) => url.set({ page: next === 1 ? null : next })}
              />
            )}
          </>
        )}
      </QueryState>
    </>
  );
}
