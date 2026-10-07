import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listEntitiesV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { OptionSelect } from "@/components/OptionSelect";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatDateTime, formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";

const SORTS = [
  { value: "mentions", label: "Упоминаний" },
  { value: "articles", label: "Публикаций" },
  { value: "recent", label: "Последняя новость" },
  { value: "name", label: "Имя" }
];
const ALL_REGIONS = "__all__";

export function EntitiesPage() {
  const url = useUrlState();
  const query = {
    q: url.get("q"),
    article: url.get("article"),
    role: url.get("role", "figurant"),
    verdict: url.get("verdict", "all"),
    region: url.get("region"),
    rf: url.get("rf", "all"),
    rf_possible: url.get("rf_possible", "all"),
    sort: url.get("sort", "mentions"),
    page: Math.max(1, url.getNumber("page", 1))
  };
  const [draft, setDraft] = useState({ q: query.q, article: query.article });
  useEffect(() => setDraft({ q: query.q, article: query.article }), [query.q, query.article]);

  const entities = useQuery({
    queryKey: ["entities", query],
    queryFn: () => unwrap(listEntitiesV1({ query })),
    placeholderData: keepPreviousData
  });

  function search(event: FormEvent) {
    event.preventDefault();
    url.set({ q: draft.q.trim(), article: draft.article.trim(), page: null });
  }

  return (
    <>
      <PageHeader
        title="Все люди"
        instruction="Люди из публикаций с уголовными делами, собранные из упоминаний: «Моора», «Моору» и «Моор» — один человек."
      />
      <form onSubmit={search} role="search" className="mb-3 flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="entity-q">Имя</Label>
          <Input
            id="entity-q"
            type="search"
            placeholder="Имя или вариант написания"
            className="w-64"
            value={draft.q}
            onChange={(event) => setDraft({ ...draft, q: event.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="entity-article">Статья УК</Label>
          <Input
            id="entity-article"
            type="search"
            placeholder="напр. 207.3"
            className="w-32"
            value={draft.article}
            onChange={(event) => setDraft({ ...draft, article: event.target.value })}
          />
        </div>
        <Button type="submit">Найти</Button>
      </form>
      <QueryState query={entities}>
        {(data) => (
          <>
            <div className="mb-3 flex flex-wrap items-end gap-3">
              <OptionSelect
                label="Роль"
                value={query.role}
                options={data.roles}
                onChange={(value) => url.set({ role: value === "figurant" ? null : value, page: null })}
              />
              <OptionSelect
                label="Вердикт"
                value={query.verdict}
                options={data.verdicts}
                onChange={(value) => url.set({ verdict: value === "all" ? null : value, page: null })}
              />
              {data.regions.length ? (
                <OptionSelect
                  label="Регион"
                  value={query.region || ALL_REGIONS}
                  options={[{ value: ALL_REGIONS, label: "Все регионы" }, ...data.regions.map((name) => ({ value: name, label: name }))]}
                  onChange={(value) => url.set({ region: value === ALL_REGIONS ? null : value, page: null })}
                />
              ) : null}
              <OptionSelect
                label="Сортировка"
                value={query.sort}
                options={SORTS}
                className="w-48"
                onChange={(value) => url.set({ sort: value === "mentions" ? null : value, page: null })}
              />
            </div>
            <div className="mb-3 flex flex-wrap gap-4 text-sm">
              <label className="flex items-center gap-2" title="ФИО с отчеством совпало с перечнем Росфинмониторинга">
                <Checkbox checked={query.rf === "hide"} onCheckedChange={(checked) => url.set({ rf: checked === true ? "hide" : null, page: null })} />
                Скрыть тех, кто в перечне РФМ ({formatNumber(data.hidden_in_list)})
              </label>
              <label className="flex items-center gap-2" title="Совпали имя и фамилия, отчества нет с одной из сторон: может быть тёзка">
                <Checkbox
                  checked={query.rf_possible === "hide"}
                  onCheckedChange={(checked) => url.set({ rf_possible: checked === true ? "hide" : null, page: null })}
                />
                Скрыть возможных — тёзки без отчества ({formatNumber(data.hidden_maybe_listed)})
              </label>
            </div>
            {data.roles_known ? null : (
              <p className="mb-2 text-sm text-amber-700">Фигуранты ещё не определены — шаг 4 в «Журнале запусков»; пока показаны все.</p>
            )}
            <p className="mb-2 text-sm text-muted-foreground">
              Найдено: {formatNumber(data.total)}
              {query.article ? ` по статье УК ${query.article}` : ""}. Серая статья УК — «общая»: в событии обвиняемыми
              названы и другие люди.{" "}
              <a className="underline" href="/ui/people/export.xlsx">
                Выгрузить всех людей в Excel
              </a>
            </p>
            {data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">Никого не найдено: ослабьте фильтры.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Имя</TableHead>
                    <TableHead>Статьи УК</TableHead>
                    <TableHead>События дела</TableHead>
                    <TableHead>Упоминаний</TableHead>
                    <TableHead>Публикаций</TableHead>
                    <TableHead>Последняя новость</TableHead>
                    <TableHead>Как писали</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell className="whitespace-normal">
                        <Link className="font-medium underline" to={dossierPath(row.key)}>
                          {row.name}
                        </Link>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {[row.name_source_label, row.rf_label, row.role_label, row.verdict_label]
                            .filter((label): label is string => Boolean(label))
                            .map((label) => (
                              <Badge key={label} variant="outline">
                                {label}
                              </Badge>
                            ))}
                        </div>
                        {row.regions.length ? <div className="text-xs text-muted-foreground">{row.regions.join(", ")}</div> : null}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        {row.articles.map((item, index) => (
                          <span key={item.article}>
                            {index ? ", " : ""}
                            <button
                              type="button"
                              className={item.shared ? "text-muted-foreground underline" : "underline"}
                              title={item.shared ? "общая" : undefined}
                              onClick={() => url.set({ article: item.article, page: null })}
                            >
                              {item.article}
                            </button>
                          </span>
                        ))}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <div className="flex flex-wrap gap-1">
                          {row.events.map((event) => (
                            <Badge key={event.kind} variant="secondary">
                              {event.label}: {event.count}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell>{formatNumber(row.mention_count)}</TableCell>
                      <TableCell>{formatNumber(row.article_count)}</TableCell>
                      <TableCell>{row.last_published_at ? formatDateTime(row.last_published_at) : DASH}</TableCell>
                      <TableCell className="whitespace-normal text-muted-foreground">{row.variants.join(", ")}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <Pager
              page={query.page}
              pageSize={data.page_size}
              shown={data.items.length}
              total={data.total}
              onPage={(next) => url.set({ page: next === 1 ? null : next })}
            />
          </>
        )}
      </QueryState>
    </>
  );
}
