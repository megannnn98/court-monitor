import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  clearUnnamedV1,
  keepUnnamedV1,
  listUnnamedV1,
  rejectUnnamedV1,
  resolveUnnamedV1,
  type UnnamedActionRequest,
  type UnnamedCardResponse
} from "@/api/generated";
import { CourtHints } from "@/components/CourtHints";
import { PageHeader } from "@/components/layout/PageHeader";
import { Pager } from "@/components/Pager";
import { QueryState } from "@/components/QueryState";
import { StatusTabs } from "@/components/StatusTabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
import { formatDate } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";
import { cn } from "@/lib/utils";

// The operator's words (`entities.unnamed`).
const SAME = "same";
const DIFFERENT = "different";
const RF_ENTRY = "rf_entry";
const EXISTING_PERSON = "existing_person";
const SUPPLIED_NAME = "supplied_name";
const NO_RF_MATCH = "no_rf_match";
const INSUFFICIENT = "insufficient";
const IDENTIFIED = [RF_ENTRY, EXISTING_PERSON, SUPPLIED_NAME];

type Action = "keep" | "reject" | "resolve" | "clear";

/** One word on a figurant: the action, then the page and its counters read again. */
function useWord() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ action, body }: { action: Action; body: UnnamedActionRequest }) =>
      unwrap({ keep: keepUnnamedV1, reject: rejectUnnamedV1, resolve: resolveUnnamedV1, clear: clearUnnamedV1 }[action]({ body })),
    onSuccess: async () => {
      await readAgainAfterDecision(client, ["unnamed"]);
    }
  });
}

function articleHref(articleId: number, start: number, end: number) {
  return `/articles/${articleId}?start=${start}&end=${end}`;
}

function Verdict({ decision }: { decision: string | null }) {
  if (decision === SAME) {
    return <Badge className="ml-1 border-emerald-500 bg-emerald-50 text-emerald-800" variant="outline">это он</Badge>;
  }
  if (decision === DIFFERENT) {
    return <Badge className="ml-1" variant="outline">не он</Badge>;
  }
  return null;
}

function Status({ card }: { card: UnnamedCardResponse }) {
  if (IDENTIFIED.includes(card.resolution)) {
    return <Badge className="border-emerald-500 bg-emerald-50 text-emerald-800" variant="outline">опознан: {card.identified_as}</Badge>;
  }
  if (card.resolution === NO_RF_MATCH) {
    return <Badge variant="outline">подходящей записи РФМ нет</Badge>;
  }
  if (card.resolution === INSUFFICIENT) {
    return <Badge variant="outline">недостаточно данных</Badge>;
  }
  return <Badge className="border-amber-400 bg-amber-50 text-amber-800" variant="outline">не разобран</Badge>;
}

function Candidates({ caption, head, children }: { caption: string; head: string[]; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">{caption}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            {head.map((name) => (
              <TableHead key={name}>{name}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>{children}</TableBody>
      </Table>
    </div>
  );
}

function Card({ card, people }: { card: UnnamedCardResponse; people: { key: string; name: string }[] }) {
  const word = useWord();
  const [person, setPerson] = useState("");
  const [name, setName] = useState("");
  const say = (action: Action, body: Omit<UnnamedActionRequest, "figurant"> = {}) => word.mutate({ action, body: { figurant: card.key, ...body } });

  return (
    <article id={`u-${card.key}`} aria-label={card.quote} className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Status card={card} />
        <span className="text-muted-foreground">{card.facts}</span>
      </div>
      <p>
        <Link className="underline" to={articleHref(card.article_id, card.start_offset, card.end_offset)}>
          {card.quote}
        </Link>{" "}
        <span className="text-sm text-muted-foreground">— {formatDate(card.published_at)}</span>
      </p>
      <p className="text-sm text-muted-foreground">Модель: {card.explanation}</p>
      {card.note ? <p className="text-sm text-muted-foreground">{card.note}</p> : null}

      {!card.has_age ? (
        <p className="text-sm text-muted-foreground">Возраст не назван — по перечню не подобрать.</p>
      ) : card.rf_candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {card.rf_total === 0
            ? `В перечне нет никого этого возраста и пола (снимок от ${formatDate(card.rf_snapshot_date)}).`
            : `В перечне ${card.rf_total} человек этого возраста и пола; без города рождения или буквы фамилии их не сузить.`}
        </p>
      ) : (
        <>
          <Candidates caption="Кандидаты из перечня" head={["ФИО", "Дата рождения", "Место рождения", "Почему подходит", "В перечне", "Решение"]}>
            {card.rf_candidates.map((item) => (
              <TableRow key={item.key}>
                <TableCell className="whitespace-normal font-medium">
                  {item.full_name}
                  <Verdict decision={item.decision} />
                </TableCell>
                <TableCell>{formatDate(item.birth_date)}</TableCell>
                <TableCell className="whitespace-normal">{item.birth_place}</TableCell>
                <TableCell className="whitespace-normal text-xs">{item.reasons.join("; ")}</TableCell>
                <TableCell className="whitespace-normal text-xs">{item.seen}</TableCell>
                <TableCell className="space-x-1 whitespace-nowrap">
                  {item.decision !== SAME ? (
                    <Button size="sm" disabled={word.isPending} onClick={() => say("resolve", { resolution: RF_ENTRY, normalized_name: item.display_name, rf_name: item.rf_name, rf_birth_date: item.birth_date })}>
                      Это он
                    </Button>
                  ) : null}
                  {item.decision !== DIFFERENT ? (
                    <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("reject", { candidate: item.key })}>
                      Не он
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </Candidates>
          {card.rf_total > card.rf_candidates.length ? (
            <p className="text-xs text-muted-foreground">
              Показаны {card.rf_candidates.length} из {card.rf_total} человек этого возраста и пола — родившиеся в названном месте или подходящие по букве фамилии.
            </p>
          ) : null}
        </>
      )}

      {card.named_candidates.length ? (
        <Candidates caption="Названные в других публикациях" head={["Человек", "Публикация, где он назван", "Почему подходит", "Решение"]}>
          {card.named_candidates.map((item) => (
            <TableRow key={item.key}>
              <TableCell className="whitespace-normal">
                <Link className="font-medium underline" to={dossierPath(item.key)}>
                  {item.name}
                </Link>
                <Verdict decision={item.decision} />
              </TableCell>
              <TableCell className="whitespace-normal">
                <Link className="underline" to={articleHref(item.article_id, item.start, item.end)}>
                  {item.title}
                </Link>
              </TableCell>
              <TableCell className="whitespace-normal text-xs">{item.reasons.join("; ")}</TableCell>
              <TableCell className="space-x-1 whitespace-nowrap">
                {item.decision !== SAME ? (
                  <Button size="sm" disabled={word.isPending} onClick={() => say("resolve", { resolution: EXISTING_PERSON, existing_person_key: item.key })}>
                    Это он
                  </Button>
                ) : null}
                {item.decision === null ? (
                  <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("reject", { candidate: item.candidate_key })}>
                    Не он
                  </Button>
                ) : item.decision === DIFFERENT ? (
                  <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("reject", { candidate: item.candidate_key, undo: true })}>
                    Вернуть
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </Candidates>
      ) : null}

      {card.base_candidates.length ? (
        <Candidates caption="Кандидаты из базы Airtable" head={["ФИО", "Дата рождения", "Регион, город", "Статьи в базе", "Почему подходит", "Решение"]}>
          {card.base_candidates.map((item) => (
            <TableRow key={item.key}>
              <TableCell className="whitespace-normal font-medium">
                {item.name}
                <Verdict decision={item.decision} />
              </TableCell>
              <TableCell>{formatDate(item.birth_date)}</TableCell>
              <TableCell className="whitespace-normal">{item.place}</TableCell>
              <TableCell className="whitespace-normal">{item.articles}</TableCell>
              <TableCell className="whitespace-normal text-xs">{item.reasons.join("; ")}</TableCell>
              <TableCell className="space-x-1 whitespace-nowrap">
                {item.decision !== SAME ? (
                  <Button size="sm" disabled={word.isPending} onClick={() => say("resolve", { resolution: SUPPLIED_NAME, normalized_name: item.name, rf_birth_date: item.birth_date })}>
                    Это он
                  </Button>
                ) : null}
                {item.decision === null ? (
                  <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("reject", { candidate: item.key })}>
                    Не он
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </Candidates>
      ) : card.base_total ? (
        <p className="text-sm text-muted-foreground">В базе Airtable {card.base_total} человек этого возраста и пола; по месту из них никто не подошёл.</p>
      ) : null}

      <CourtHints courts={card.courts} total={card.courts_total} />

      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        {people.length ? (
          <>
            <select aria-label="Существующий человек" className="h-8 rounded-md border bg-background px-2 text-sm" value={person} onChange={(event) => setPerson(event.target.value)}>
              <option value="">Найти существующего человека</option>
              {people.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.name}
                </option>
              ))}
            </select>
            <Button size="sm" variant="outline" disabled={!person || word.isPending} onClick={() => say("resolve", { resolution: EXISTING_PERSON, existing_person_key: person })}>
              Это он
            </Button>
          </>
        ) : null}
        <form
          className="flex items-center gap-1"
          onSubmit={(event) => {
            event.preventDefault();
            say("resolve", { resolution: SUPPLIED_NAME, normalized_name: name.trim() });
          }}
        >
          <Input aria-label="Имя" placeholder="Имя" maxLength={200} className="h-8 w-56" value={name} onChange={(event) => setName(event.target.value)} />
          <Button type="submit" size="sm" variant="outline" disabled={!name.trim() || word.isPending}>
            Создать человека
          </Button>
        </form>
        <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("resolve", { resolution: NO_RF_MATCH })}>
          Подходящей записи РФМ нет
        </Button>
        <Button size="sm" variant="outline" disabled={word.isPending} onClick={() => say("resolve", { resolution: INSUFFICIENT })}>
          Недостаточно данных
        </Button>
        {card.resolution ? (
          <Button size="sm" variant="ghost" disabled={word.isPending} onClick={() => say("clear")}>
            Отменить решение
          </Button>
        ) : null}
        {word.isError ? <span className="text-sm text-destructive">{word.error.message}</span> : null}
      </div>
    </article>
  );
}

function Search({ label, value, onSearch }: { label: string; value: string; onSearch: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  function submit(event: FormEvent) {
    event.preventDefault();
    onSearch(draft.trim());
  }
  return (
    <form role="search" onSubmit={submit} className="flex gap-2">
      <Input type="search" aria-label={label} placeholder={label} className="w-72" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <Button type="submit" variant="outline">
        Найти
      </Button>
    </form>
  );
}

export function UnnamedPage() {
  const url = useUrlState();
  const query = {
    status: url.get("status", "open"),
    page: Math.max(1, url.getNumber("page", 1)),
    person_q: url.get("person_q"),
    rf_q: url.get("rf_q"),
    rf_key: url.get("rf_key")
  };
  const result = useQuery({
    queryKey: ["unnamed", query],
    queryFn: () => unwrap(listUnnamedV1({ query })),
    placeholderData: keepPreviousData
  });
  const keep = useWord();

  // A link to one card (from «Результат»): shown once its card is drawn.
  const loaded = Boolean(result.data);
  useEffect(() => {
    if (loaded && window.location.hash) {
      document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView();
    }
  }, [loaded]);

  return (
    <>
      <PageHeader title="Безымянные" instruction="Фигуранты, которых публикация не называет, и кто из перечня Росфинмониторинга может быть ими." />
      <QueryState query={result}>
        {(data) => (
          <>
            <StatusTabs options={data.statuses} value={query.status} onChange={(value) => url.set({ status: value === "open" ? null : value, page: null })} />
            <div className="mb-3 flex flex-wrap items-center gap-4">
              <Search label="Имя существующего человека" value={query.person_q} onSearch={(value) => url.set({ person_q: value })} />
              <Search label="Имя из перечня РФМ" value={query.rf_q} onSearch={(value) => url.set({ rf_q: value, rf_key: null })} />
              <Link className="ml-auto text-sm underline" to="/base-unnamed">
                Без имени в базе Airtable
              </Link>
            </div>
            {query.person_q && !data.people.length ? <p className="mb-2 text-sm text-muted-foreground">Существующих людей с таким именем нет.</p> : null}
            {query.rf_q ? (
              data.rf_entries.length ? (
                <section className="mb-3 rounded-lg border bg-card p-3 text-sm">
                  <h2 className="mb-1 font-medium">Записи РФМ</h2>
                  <ul className="space-y-0.5">
                    {data.rf_entries.map((entry) => (
                      <li key={`${entry.full_name}-${entry.birth_date}`}>
                        {entry.key ? (
                          <button type="button" className={cn("underline", entry.key === query.rf_key && "font-medium")} onClick={() => url.set({ rf_key: entry.key })}>
                            {entry.full_name}
                          </button>
                        ) : (
                          entry.full_name
                        )}{" "}
                        · {entry.birth_date ? formatDate(entry.birth_date) : "дата рождения неизвестна"}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : (
                <p className="mb-3 text-sm text-muted-foreground">В актуальном снимке РФМ совпадений нет.</p>
              )
            ) : null}
            {query.rf_key ? (
              <section className="mb-3 rounded-lg border bg-card p-3 text-sm">
                <h2 className="mb-1 font-medium">Подходящие безымянные ({data.reverse.length})</h2>
                <p className="mb-1 text-muted-foreground">По тем же правилам возраста, пола, инициалов и места; решение не принято.</p>
                <ul className="space-y-0.5">
                  {data.reverse.map((item) => (
                    <li key={item.key}>
                      <a className="underline" href={`#u-${item.key}`}>
                        {item.quote}
                      </a>{" "}
                      <span className="text-muted-foreground">{item.facts}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {data.items.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                {data.none_yet ? "Безымянных фигурантов пока нет: их находит шаг 5 «Отобрать политические дела»." : "В этом разделе никого."}
              </p>
            ) : (
              <div className="space-y-4">
                {data.items.map((card) => (
                  <Card key={card.key} card={card} people={data.people} />
                ))}
              </div>
            )}
            <Pager page={query.page} pageSize={data.page_size} shown={data.items.length} total={data.total} onPage={(next) => url.set({ page: next === 1 ? null : next })} />
            {data.screened.length ? (
              <details className="mt-4 rounded-lg border bg-card p-3">
                <summary className="cursor-pointer text-sm font-medium">Отсеяно автоматически ({data.screened.length})</summary>
                <p className="my-2 text-sm text-muted-foreground">
                  Поиск сам убрал этих людей с разбора: дело — обычная уголовщина, либо о человеке уже есть карточка по другому предложению той же публикации. Если кто-то убран зря, верните его.
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Предложение</TableHead>
                      <TableHead>Дата</TableHead>
                      <TableHead>Что известно</TableHead>
                      <TableHead>Почему отсеяно</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.screened.map((item) => (
                      <TableRow key={item.key}>
                        <TableCell className="whitespace-normal">
                          <Link className="underline" to={articleHref(item.article_id, item.start_offset, item.end_offset)}>
                            {item.quote}
                          </Link>
                        </TableCell>
                        <TableCell>{formatDate(item.published_at)}</TableCell>
                        <TableCell className="whitespace-normal text-xs">{item.facts}</TableCell>
                        <TableCell className="whitespace-normal text-xs">
                          {item.reason}
                          <div className="text-muted-foreground">{item.explanation}</div>
                        </TableCell>
                        <TableCell>
                          <Button size="sm" variant="outline" disabled={keep.isPending} onClick={() => keep.mutate({ action: "keep", body: { figurant: item.key } })}>
                            Вернуть на разбор
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </details>
            ) : null}
            <details className="mt-4 text-sm text-muted-foreground">
              <summary className="cursor-pointer select-none">Как читать карточку</summary>
              <ul className="mt-2 list-disc space-y-1 pl-5">
                <li>
                  Кандидаты из перечня — того возраста на дату новости (или на год старше), того пола и буквы фамилии; сначала родившиеся в названном месте. Место рождения — не всегда место
                  жительства.
                </li>
                <li>
                  «Названные в других публикациях» — люди, которых в те же дни (±3) назвала другая публикация о том же месте на той же стадии дела. Это наводка: возраст рядом с именем может
                  относиться к соседнему человеку.
                </li>
                <li>Кандидаты из базы Airtable — того возраста и пола из названного места; к новости о приговоре добавляются люди базы с приговором по той же статье за 90 дней.</li>
                <li>Опознанный человек попадёт в результат после следующих шагов 3–5.</li>
              </ul>
            </details>
          </>
        )}
      </QueryState>
    </>
  );
}
