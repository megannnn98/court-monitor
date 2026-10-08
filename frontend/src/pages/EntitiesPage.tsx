import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listEntitiesV1, type EntityRowResponse, type OptionResponse } from "@/api/generated";
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
import { DASH, formatDate, formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";
import { cn } from "@/lib/utils";

const SORTS = [
  { value: "mentions", label: "Упоминаний" },
  { value: "articles", label: "Публикаций" },
  { value: "recent", label: "Последняя новость" },
  { value: "name", label: "Имя" }
];
const DEFAULT_ROLE = "figurant";

/** One filter as a list to click: the chosen value stands out, a count follows the label. */
function Facet({
  title,
  options,
  value,
  onChange
}: {
  title: string;
  options: OptionResponse[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <section aria-label={title}>
      <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
      <ul className="space-y-0.5">
        {options.map((option) => (
          <li key={option.value}>
            <button
              type="button"
              aria-pressed={option.value === value}
              onClick={() => onChange(option.value)}
              className={cn(
                "flex w-full justify-between gap-2 rounded px-2 py-1 text-left text-sm",
                option.value === value ? "bg-primary text-primary-foreground" : "hover:bg-muted"
              )}
            >
              <span>{option.label}</span>
              {option.count === null || option.count === undefined ? null : (
                <span className="opacity-70">{formatNumber(option.count)}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Marks({ row }: { row: EntityRowResponse }) {
  const political = row.verdict === "political";
  return (
    <span className="inline-flex flex-wrap gap-1">
      {row.verdict_label ? (
        <Badge variant="outline" className={political ? "border-rose-400 bg-rose-50 text-rose-800" : undefined}>
          {row.verdict_label}
        </Badge>
      ) : null}
      {row.rf_label ? (
        <Badge variant="outline" className="border-violet-400 bg-violet-50 text-violet-800">
          {row.rf_label}
        </Badge>
      ) : null}
      {row.role && row.role !== DEFAULT_ROLE && row.role_label ? <Badge variant="outline">{row.role_label}</Badge> : null}
    </span>
  );
}

export function EntitiesPage() {
  const url = useUrlState();
  const query = {
    q: url.get("q"),
    article: url.get("article"),
    role: url.get("role", DEFAULT_ROLE),
    verdict: url.get("verdict", "all"),
    region: url.get("region"),
    rf: url.get("rf", "all"),
    rf_possible: url.get("rf_possible", "all"),
    sort: url.get("sort", "mentions"),
    page: Math.max(1, url.getNumber("page", 1))
  };
  const [draft, setDraft] = useState({ q: query.q, article: query.article });
  useEffect(() => setDraft({ q: query.q, article: query.article }), [query.q, query.article]);
  const [regionSearch, setRegionSearch] = useState("");

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
      <PageHeader title="Все люди" />
      <QueryState query={entities}>
        {(data) => {
          const label = (options: OptionResponse[], value: string) => options.find((option) => option.value === value)?.label ?? value;
          // What is chosen now, each a chip that takes it back.
          const chips: [string, string, boolean][] = [
            [`имя: ${query.q}`, "q", Boolean(query.q)],
            [`статья ${query.article}`, "article", Boolean(query.article)],
            [query.region, "region", Boolean(query.region)],
            [label(data.verdicts, query.verdict), "verdict", query.verdict !== "all"],
            [label(data.roles, query.role), "role", query.role !== DEFAULT_ROLE],
            ["без тех, кто в перечне", "rf", query.rf === "hide"],
            ["без тёзок", "rf_possible", query.rf_possible === "hide"]
          ];
          const regions = data.regions.filter((name) => !regionSearch || name.toLowerCase().includes(regionSearch.toLowerCase()));
          return (
            <div className="grid items-start gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
              <aside aria-label="Фильтры" className="space-y-5 lg:sticky lg:top-4">
                <form onSubmit={search} role="search" className="space-y-3">
                  <div className="space-y-1">
                    <Label htmlFor="entity-q">Имя</Label>
                    <Input
                      id="entity-q"
                      type="search"
                      placeholder="Имя или как писали"
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
                      value={draft.article}
                      onChange={(event) => setDraft({ ...draft, article: event.target.value })}
                    />
                  </div>
                  <Button type="submit" className="w-full">
                    Найти
                  </Button>
                </form>
                <Facet
                  title="Роль"
                  options={data.roles}
                  value={query.role}
                  onChange={(value) =>
                    url.set({
                      role: value === DEFAULT_ROLE ? null : value,
                      page: null
                    })
                  }
                />
                <Facet
                  title="Вердикт"
                  options={data.verdicts}
                  value={query.verdict}
                  onChange={(value) =>
                    url.set({
                      verdict: value === "all" ? null : value,
                      page: null
                    })
                  }
                />
                <section aria-label="Перечень РФМ" className="space-y-2 text-sm">
                  <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Перечень РФМ</h2>
                  <label className="flex items-center gap-2" title="ФИО с отчеством совпало с перечнем Росфинмониторинга">
                    <Checkbox
                      checked={query.rf === "hide"}
                      onCheckedChange={(checked) =>
                        url.set({
                          rf: checked === true ? "hide" : null,
                          page: null
                        })
                      }
                    />
                    Скрыть, кто в перечне ({formatNumber(data.hidden_in_list)})
                  </label>
                  <label
                    className="flex items-center gap-2"
                    title="Совпали имя и фамилия, отчества нет с одной из сторон: может быть тёзка"
                  >
                    <Checkbox
                      checked={query.rf_possible === "hide"}
                      onCheckedChange={(checked) =>
                        url.set({
                          rf_possible: checked === true ? "hide" : null,
                          page: null
                        })
                      }
                    />
                    Скрыть тёзок без отчества ({formatNumber(data.hidden_maybe_listed)})
                  </label>
                </section>
                {data.regions.length ? (
                  <section aria-label="Регион">
                    <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Регион</h2>
                    <Input
                      aria-label="Найти регион"
                      className="mb-1 h-8"
                      placeholder="найти регион"
                      value={regionSearch}
                      onChange={(event) => setRegionSearch(event.target.value)}
                    />
                    <ul className="max-h-64 space-y-0.5 overflow-y-auto">
                      {["", ...regions].map((name) => (
                        <li key={name || "all"}>
                          <button
                            type="button"
                            aria-pressed={(query.region || "") === name}
                            onClick={() => url.set({ region: name || null, page: null })}
                            className={cn(
                              "w-full rounded px-2 py-0.5 text-left text-sm",
                              (query.region || "") === name ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                            )}
                          >
                            {name || "Все регионы"}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
              </aside>
              <div>
                <div className="mb-2 flex flex-wrap items-center gap-3">
                  <span className="text-sm font-medium">Найдено: {formatNumber(data.total)}</span>
                  {chips
                    .filter(([, , on]) => on)
                    .map(([text, name]) => (
                      <button
                        key={name}
                        type="button"
                        aria-label={`Убрать фильтр: ${text}`}
                        onClick={() => url.set({ [name]: null, page: null })}
                        className="rounded-full border bg-muted px-2 py-0.5 text-xs hover:bg-background"
                      >
                        {text} ✕
                      </button>
                    ))}
                  <span className="ml-auto flex items-end gap-3">
                    <OptionSelect
                      label="Сортировка"
                      value={query.sort}
                      options={SORTS}
                      className="h-8 w-48"
                      onChange={(value) =>
                        url.set({
                          sort: value === "mentions" ? null : value,
                          page: null
                        })
                      }
                    />
                    <a className="pb-1 text-sm underline" href="/ui/people/export.xlsx">
                      Выгрузить всех в Excel
                    </a>
                  </span>
                </div>
                {data.roles_known ? null : (
                  <p className="mb-2 text-sm text-amber-700">
                    Фигуранты ещё не определены — шаг 4 в «Журнале запусков»; пока показаны все.
                  </p>
                )}
                {data.items.length === 0 ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">Никого не найдено: ослабьте фильтры.</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Человек</TableHead>
                        <TableHead>Статьи УК</TableHead>
                        <TableHead>События дела</TableHead>
                        <TableHead className="text-right">Упом.</TableHead>
                        <TableHead className="text-right">Публ.</TableHead>
                        <TableHead>Последняя новость</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.items.map((row) => (
                        <TableRow key={row.id}>
                          <TableCell className="whitespace-normal">
                            <Link className="font-medium hover:underline" to={dossierPath(row.key)}>
                              {row.name}
                            </Link>{" "}
                            <Marks row={row} />
                            {/* The spellings and where the name came from, in the hint: the row keeps one short line. */}
                            <div
                              className="text-xs text-muted-foreground"
                              title={[
                                row.variants.length ? `Как писали: ${row.variants.join(", ")}` : "",
                                row.name_source_label ? `Имя: ${row.name_source_label}` : ""
                              ]
                                .filter(Boolean)
                                .join("\n")}
                            >
                              {[row.regions.join(", "), row.variants.length > 1 ? `${row.variants.length} написания` : ""]
                                .filter(Boolean)
                                .join(" · ")}
                            </div>
                          </TableCell>
                          <TableCell className="whitespace-normal">
                            {row.articles.length
                              ? row.articles.map((item, index) => (
                                  <span key={item.article}>
                                    {index ? ", " : ""}
                                    <button
                                      type="button"
                                      className={cn("hover:underline", item.shared && "text-muted-foreground")}
                                      title={item.shared ? "общая: в событии обвиняемыми названы и другие люди" : "все люди по этой статье"}
                                      onClick={() =>
                                        url.set({
                                          article: item.article,
                                          page: null
                                        })
                                      }
                                    >
                                      {item.article}
                                    </button>
                                  </span>
                                ))
                              : DASH}
                          </TableCell>
                          <TableCell className="whitespace-normal text-xs text-muted-foreground">
                            {row.events.map((event) => `${event.label} ${event.count}`).join(" · ") || DASH}
                          </TableCell>
                          <TableCell className="text-right">{formatNumber(row.mention_count)}</TableCell>
                          <TableCell className="text-right">{formatNumber(row.article_count)}</TableCell>
                          <TableCell>{formatDate(row.last_published_at)}</TableCell>
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
                <details className="mt-4 text-sm text-muted-foreground">
                  <summary className="cursor-pointer select-none">Как читать список</summary>
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    <li>Человек собран из упоминаний: «Моора», «Моору» и «Моор» — один человек.</li>
                    <li>Серая статья УК — «общая»: в событии обвиняемыми названы и другие люди. Клик по статье — все люди по ней.</li>
                    <li>«В перечне» — ФИО с отчеством совпало с перечнем Росфинмониторинга; «возможно» — только имя и фамилия.</li>
                  </ul>
                </details>
              </div>
            </div>
          );
        }}
      </QueryState>
    </>
  );
}
