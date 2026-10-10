import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listEntitiesV1, listRemovalsV1, setRemovalV1, type EntityRowResponse, type OptionResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { OptionSelect } from "@/components/OptionSelect";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
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
// The select has no empty value.
const ALL_REGIONS = "all-regions";

/** The few words next to a name: what the case is, the list, an unusual role. */
function Marks({ row }: { row: EntityRowResponse }) {
  const political = row.verdict === "political";
  return (
    <div className="mt-1 flex flex-wrap gap-1">
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
    </div>
  );
}

function Articles({ row, onArticle }: { row: EntityRowResponse; onArticle: (article: string) => void }) {
  if (!row.articles.length) {
    return <>{DASH}</>;
  }
  return (
    <>
      {row.articles.map((item, index) => (
        <span key={item.article}>
          {index ? ", " : ""}
          <button
            type="button"
            className={cn("hover:underline", item.shared && "text-muted-foreground")}
            title={item.shared ? "общая: в событии обвиняемыми названы и другие люди" : "все люди по этой статье"}
            onClick={(event) => {
              event.stopPropagation();
              onArticle(item.article);
            }}
          >
            {item.article}
          </button>
        </span>
      ))}
    </>
  );
}

/** Everything of one person the table leaves out. */
function Panel({ row, onArticle }: { row: EntityRowResponse; onArticle: (article: string) => void }) {
  return (
    <aside aria-label={`Подробно: ${row.name}`} className="space-y-4 rounded-lg border bg-card p-4 text-sm xl:sticky xl:top-4">
      <div>
        <div className="text-base">
          <Link className="font-medium underline" to={dossierPath(row.key)}>
            {row.name}
          </Link>
        </div>
        <Marks row={row} />
        <div className="mt-1 text-muted-foreground">
          {row.regions.join(", ") || DASH} · {formatDate(row.last_published_at)}
        </div>
      </div>
      {row.variants.length || row.name_source_label ? (
        <section>
          <h3 className="mb-1 font-medium">Как писали</h3>
          {row.variants.length ? <p className="text-muted-foreground">{row.variants.join(", ")}</p> : null}
          {row.name_source_label ? <p className="text-muted-foreground">Имя: {row.name_source_label}</p> : null}
        </section>
      ) : null}
      <section>
        <h3 className="mb-1 font-medium">Статьи УК</h3>
        <p>
          <Articles row={row} onArticle={onArticle} />
        </p>
      </section>
      <section>
        <h3 className="mb-1 font-medium">События дела</h3>
        <p className="text-muted-foreground">{row.events.map((event) => `${event.label} ${event.count}`).join(" · ") || DASH}</p>
      </section>
      <section>
        <h3 className="mb-1 font-medium">Публикации</h3>
        <p className="text-muted-foreground">
          Упоминаний: {formatNumber(row.mention_count)} · публикаций: {formatNumber(row.article_count)}
        </p>
        <Link className="underline" to={dossierPath(row.key)}>
          Открыть досье
        </Link>
      </section>
    </aside>
  );
}

/** The filters used less often: in the line on a wide screen; on a phone they are a
 * screen of choices before the first person, so they fold under one line. */
function Folded({ children }: { children: ReactNode }) {
  // Tailwind's `sm`.
  const inLine = useMediaQuery("(min-width: 640px)", true);
  if (inLine) {
    return <>{children}</>;
  }
  return (
    <details className="w-full rounded-md border bg-card p-3">
      <summary className="cursor-pointer text-sm font-medium">Ещё фильтры: регион, перечень, сортировка</summary>
      <div className="mt-3 flex flex-wrap items-end gap-3">{children}</div>
    </details>
  );
}

/** The people removed by hand as nobody («Удалить: такого человека нет» in a dossier),
 * each with a way back. Nothing while there are none. */
function Removed() {
  const client = useQueryClient();
  const removed = useQuery({
    queryKey: ["entities", "removals"],
    queryFn: () => unwrap(listRemovalsV1())
  });
  const restore = useMutation({
    mutationFn: (key: string) => unwrap(setRemovalV1({ body: { key, removed: false } })),
    onSuccess: async () => {
      await readAgainAfterDecision(client, ["entities"]);
    }
  });
  const items = removed.data?.items ?? [];
  if (!items.length && !restore.isSuccess) {
    return null;
  }
  return (
    <details className="mt-2 text-sm text-muted-foreground" open={restore.isSuccess || undefined}>
      <summary className="cursor-pointer select-none">Удалённые вручную ({formatNumber(items.length)})</summary>
      {restore.isSuccess ? (
        <p role="status" className="mt-2 text-foreground">
          «{restore.data.name}» возвращён: появится в списке после следующего шага 3 «Собрать сущности».
        </p>
      ) : null}
      {restore.isError ? (
        <p role="alert" className="mt-2 text-destructive">
          {restore.error.message}
        </p>
      ) : null}
      <ul className="mt-2 space-y-1">
        {items.map((item) => (
          <li key={item.key} className="flex flex-wrap items-center gap-2">
            <span className="text-foreground">{item.name}</span>
            <Button size="sm" variant="outline" disabled={restore.isPending} onClick={() => restore.mutate(item.key)}>
              Вернуть
            </Button>
          </li>
        ))}
      </ul>
    </details>
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
  const [selected, setSelected] = useState<string | null>(null);
  // Tailwind's `xl`: from there the panel stands beside the table.
  const beside = useMediaQuery("(min-width: 1280px)", true);
  const byArticle = (article: string) => url.set({ article, page: null });

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
      >
        {/* The list is not the result: said in sight, where a common criminal's name surprised. */}
        {query.verdict === "all" ? (
          <p className="max-w-3xl text-sm text-muted-foreground">
            Здесь все, на кого заведено уголовное дело: и политические дела, и обычные уголовные. Какое дело — написано рядом с
            именем. Только политические — в{" "}
            <Link className="underline" to="/political">
              «Результатах»
            </Link>{" "}
            или здесь{" "}
            <button type="button" className="underline" onClick={() => url.set({ verdict: "political", page: null })}>
              фильтром «Вердикт: политические»
            </button>
            .
          </p>
        ) : null}
      </PageHeader>
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
          const tapped = data.items.find((row) => row.key === selected);
          const shown = tapped ?? data.items[0];
          const marked = (beside ? shown : tapped)?.key;
          return (
            <>
              <nav aria-label="Роль" className="mb-1 flex flex-wrap border-b">
                {data.roles.map((role) => {
                  const active = role.value === query.role;
                  return (
                    <button
                      key={role.value}
                      type="button"
                      aria-pressed={active}
                      onClick={() => url.set({ role: role.value === DEFAULT_ROLE ? null : role.value, page: null })}
                      className={cn(
                        "-mb-px border-b-2 px-4 py-2 text-sm",
                        active ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {role.label}
                      {role.count === null || role.count === undefined ? null : (
                        <span className="ml-1 rounded-full bg-muted px-2 text-xs">{formatNumber(role.count)}</span>
                      )}
                    </button>
                  );
                })}
              </nav>
              <div className="flex flex-wrap items-center gap-2 py-2" role="group" aria-label="Вердикт">
                {data.verdicts.map((verdict) => {
                  const active = verdict.value === query.verdict;
                  return (
                    <Button
                      key={verdict.value}
                      size="sm"
                      variant={active ? "default" : "ghost"}
                      aria-pressed={active}
                      onClick={() => url.set({ verdict: verdict.value === "all" ? null : verdict.value, page: null })}
                    >
                      {verdict.label}
                      {verdict.count === null || verdict.count === undefined ? null : (
                        <span className="ml-1 opacity-70">{formatNumber(verdict.count)}</span>
                      )}
                    </Button>
                  );
                })}
                <span className="ml-auto text-sm text-muted-foreground">
                  Найдено: {formatNumber(data.total)} ·{" "}
                  <a className="underline" href="/ui/people/export.xlsx">
                    Выгрузить всех в Excel
                  </a>
                </span>
              </div>
              <div aria-label="Фильтры" role="group" className="mb-3 flex flex-wrap items-end gap-3">
                <form onSubmit={search} role="search" className="flex flex-wrap items-end gap-2">
                  <div className="space-y-1">
                    <Label htmlFor="entity-q">Имя</Label>
                    <Input
                      id="entity-q"
                      type="search"
                      className="h-8 w-48"
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
                      className="h-8 w-28"
                      placeholder="напр. 207.3"
                      value={draft.article}
                      onChange={(event) => setDraft({ ...draft, article: event.target.value })}
                    />
                  </div>
                  <Button type="submit" size="sm" variant="outline">
                    Найти
                  </Button>
                </form>
                <Folded>
                  {data.regions.length ? (
                    <OptionSelect
                      label="Регион"
                      value={query.region || ALL_REGIONS}
                      options={[{ value: ALL_REGIONS, label: "Все регионы" }, ...data.regions.map((name) => ({ value: name, label: name }))]}
                      className="h-8 w-52"
                      onChange={(value) => url.set({ region: value === ALL_REGIONS ? null : value, page: null })}
                    />
                  ) : null}
                  <OptionSelect
                    label="Сортировка"
                    value={query.sort}
                    options={SORTS}
                    className="h-8 w-48"
                    onChange={(value) => url.set({ sort: value === "mentions" ? null : value, page: null })}
                  />
                  <div className="space-y-1 text-sm">
                    <label className="flex items-center gap-2" title="ФИО с отчеством совпало с перечнем Росфинмониторинга">
                      <Checkbox
                        checked={query.rf === "hide"}
                        onCheckedChange={(checked) => url.set({ rf: checked === true ? "hide" : null, page: null })}
                      />
                      Скрыть, кто в перечне ({formatNumber(data.hidden_in_list)})
                    </label>
                    <label
                      className="flex items-center gap-2"
                      title="Совпали имя и фамилия, отчества нет с одной из сторон: может быть тёзка"
                    >
                      <Checkbox
                        checked={query.rf_possible === "hide"}
                        onCheckedChange={(checked) => url.set({ rf_possible: checked === true ? "hide" : null, page: null })}
                      />
                      Скрыть тёзок без отчества ({formatNumber(data.hidden_maybe_listed)})
                    </label>
                  </div>
                </Folded>
              </div>
              {chips.some(([, , on]) => on) ? (
                <div className="mb-2 flex flex-wrap items-center gap-2">
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
                </div>
              ) : null}
              {data.roles_known ? null : (
                <p className="mb-2 text-sm text-amber-700">
                  Фигуранты ещё не определены — шаг 4 в «Журнале запусков»; пока показаны все.
                </p>
              )}
              {data.items.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">Никого не найдено: ослабьте фильтры.</p>
              ) : (
                <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 xl:grid-cols-[minmax(0,1fr)_20rem] 2xl:grid-cols-[minmax(0,1fr)_24rem]">
                  <div>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Человек</TableHead>
                          <TableHead>Статьи УК</TableHead>
                          {/* On a phone: who, the articles, the latest news; the rest is a tap away. */}
                          <TableHead className="hidden md:table-cell">Регион</TableHead>
                          <TableHead className="w-24 leading-tight whitespace-normal">Последняя новость</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.items.map((row) => (
                          <TableRow
                            key={row.id}
                            aria-selected={row.key === marked}
                            onClick={() => setSelected(row.key)}
                            className={cn("cursor-pointer", row.key === marked && "bg-muted")}
                          >
                            <TableCell className="whitespace-normal">
                              <Link className="font-medium underline" to={dossierPath(row.key)} onClick={(event) => event.stopPropagation()}>
                                {row.name}
                              </Link>
                              <Marks row={row} />
                            </TableCell>
                            <TableCell className="max-w-40 whitespace-normal">
                              <Articles row={row} onArticle={byArticle} />
                            </TableCell>
                            <TableCell className="hidden max-w-40 truncate md:table-cell" title={row.regions.join(", ")}>
                              {row.regions.join(", ") || DASH}
                            </TableCell>
                            <TableCell>{formatDate(row.last_published_at)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    <Pager
                      page={query.page}
                      pageSize={data.page_size}
                      shown={data.items.length}
                      total={data.total}
                      onPage={(next) => url.set({ page: next === 1 ? null : next })}
                    />
                  </div>
                  {/* Beside the table where there is room; on a narrower screen the panel opens
                      over it for the row tapped (as on «Результат»). */}
                  {beside ? (
                    shown ? <Panel row={shown} onArticle={byArticle} /> : null
                  ) : (
                    <Sheet open={tapped !== undefined} onOpenChange={(open) => (open ? null : setSelected(null))}>
                      <SheetContent side="bottom" className="max-h-[85vh] overflow-y-auto">
                        <SheetHeader className="sr-only">
                          <SheetTitle>{tapped?.name ?? "Подробно"}</SheetTitle>
                        </SheetHeader>
                        {tapped ? <Panel row={tapped} onArticle={byArticle} /> : null}
                      </SheetContent>
                    </Sheet>
                  )}
                </div>
              )}
              <details className="mt-4 text-sm text-muted-foreground">
                <summary className="cursor-pointer select-none">Как читать список</summary>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  <li>Человек собран из упоминаний: «Моора», «Моору» и «Моор» — один человек.</li>
                  <li>Серая статья УК — «общая»: в событии обвиняемыми названы и другие люди. Клик по статье — все люди по ней.</li>
                  <li>«В перечне» — ФИО с отчеством совпало с перечнем Росфинмониторинга; «возможно» — только имя и фамилия.</li>
                </ul>
              </details>
            </>
          );
        }}
      </QueryState>
      <Removed />
    </>
  );
}
