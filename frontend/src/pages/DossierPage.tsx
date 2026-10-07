import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getDossierV1, type QuoteResponse } from "@/api/generated";
import { EventGraph } from "@/components/EventGraph";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { unwrap } from "@/lib/api";
import { DASH, externalUrl, formatDate, formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";

/** The excerpt with the mention marked, as the legacy dossier writes it. */
function Excerpt({ quote, start, end }: Pick<QuoteResponse, "quote" | "start" | "end">) {
  return (
    <p className="text-sm text-muted-foreground">
      …{quote.slice(0, start)}
      <mark>{quote.slice(start, end)}</mark>
      {quote.slice(end)}…
    </p>
  );
}

function articleHref(source: Pick<QuoteResponse, "article_id" | "text_start" | "text_end">): string {
  return `/articles/${source.article_id}?start=${source.text_start}&end=${source.text_end}`;
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="mb-8">
      <h2 id={`${id}-title`} className="mb-3 text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-muted-foreground">{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DossierPage() {
  const key = useParams().personKey ?? "";
  const dossier = useQuery({
    queryKey: ["investigations", "dossier", key],
    queryFn: () => unwrap(getDossierV1({ path: { key } }))
  });

  return (
    <QueryState query={dossier}>
      {(person) => (
        <>
          <p className="mb-2 text-sm">
            <Link className="underline" to="/investigations">
              ← Найти человека
            </Link>
          </p>
          <PageHeader title={person.name} instruction="Досье: кто это, что и когда произошло, на каких публикациях основан вывод.">
            <div className="flex flex-wrap gap-1">
              <Badge variant="outline">{person.role_label ?? "роль не определена"}</Badge>
              <Badge variant="outline">{person.verdict_label ? `дело: ${person.verdict_label}` : "политичность не оценивалась"}</Badge>
              <Badge variant="outline">{person.rf_label}</Badge>
              <Badge variant="outline">
                {person.disputes.length ? `нерешённых спорных пар: ${person.disputes.length}` : "спорных пар нет"}
              </Badge>
            </div>
            <p className="text-sm">
              Ручные решения (исправить имя, «должностное лицо») — в{" "}
              <a className="underline" href={`/ui/investigations/${encodeURIComponent(person.key)}`}>
                старом интерфейсе
              </a>
              .
            </p>
          </PageHeader>

          <Card className="mb-6">
            <CardContent>
              <Facts
                rows={[
                  ["Как писали", person.variants.map((form) => `${form.form} (${form.count})`).join(", ") || DASH],
                  ["Регион", person.regions.join(", ") || "не указан"],
                  ["Публикаций", `${formatNumber(person.article_count)} · упоминаний: ${formatNumber(person.mention_count)}`],
                  ["Первая публикация", formatDate(person.first_published_at)],
                  ["Последняя публикация", formatDate(person.last_published_at)],
                  ["События дела", person.events.map((event) => `${event.label}: ${event.count}`).join(", ") || DASH],
                  ["Имя", `${person.name_source_label}${person.gender_label ? ` · ${person.gender_label}` : ""}`]
                ]}
              />
            </CardContent>
          </Card>

          <Section id="decision" title="Решение системы">
            <div className="mb-4 grid gap-4 md:grid-cols-3">
              <Card>
                <CardHeader>
                  <CardTitle>Политичность дела</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {person.verdict ? (
                    <>
                      <p>
                        <Badge variant="outline">{person.verdict_label}</Badge>{" "}
                        <span className="text-muted-foreground">— {person.verdict_method_label}</span>
                      </p>
                      <p>
                        <b>Причина:</b> {person.verdict_reason}
                      </p>
                      {person.verdict_quote ? <blockquote className="border-l-2 pl-3 italic">{person.verdict_quote}</blockquote> : null}
                      <p className="text-muted-foreground">
                        Доказательство:{" "}
                        {person.verdict_source_article_id ? (
                          <Link className="underline" to={`/articles/${person.verdict_source_article_id}`}>
                            исходная публикация
                          </Link>
                        ) : (
                          <a className="underline" href="#evidence">
                            публикации ниже
                          </a>
                        )}
                        .
                      </p>
                    </>
                  ) : (
                    <p className="text-muted-foreground">Шаг 5 не оценивал это дело: оценивают только фигурантов уголовных дел.</p>
                  )}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Роль в деле</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {person.role ? (
                    <>
                      <p>
                        <Badge variant="outline">{person.role_label}</Badge>{" "}
                        <span className="text-muted-foreground">— {person.role_method_label}</span>
                      </p>
                      <p>
                        <b>Причина:</b> {person.role_reason}
                      </p>
                      {person.role_quote ? <blockquote className="border-l-2 pl-3 italic">{person.role_quote}</blockquote> : null}
                    </>
                  ) : (
                    <p className="text-muted-foreground">Шаг 5 ещё не определял роль.</p>
                  )}
                  {person.news ? (
                    <div>
                      <h3 className="font-medium">Свежая новость</h3>
                      <Badge variant="outline">{person.news.label}</Badge>
                      <p className="text-muted-foreground">{person.news.reason}</p>
                    </div>
                  ) : null}
                  {person.known.loaded ? (
                    <div>
                      <h3 className="font-medium">База Airtable</h3>
                      <Badge variant="outline">{person.known.not_in_base ? "нет в базе" : person.known.label}</Badge>
                      {person.known.names.length ? (
                        <ul className="list-disc pl-5">
                          {person.known.names.map((name) => (
                            <li key={name}>{name}</li>
                          ))}
                          {person.known.more ? <li className="text-muted-foreground">…и ещё {person.known.more}</li> : null}
                        </ul>
                      ) : null}
                      <p className="text-muted-foreground">Сверка только по имени: даты рождения и региона в таблице нет.</p>
                    </div>
                  ) : null}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Росфинмониторинг</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <Badge variant="outline">{person.rf_label}</Badge>
                  {person.rf_entries.length ? (
                    <ul className="space-y-1">
                      {person.rf_entries.map((entry) => (
                        <li key={entry.text}>
                          <Badge variant="secondary">{entry.level_label}</Badge> {entry.text}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {person.rf_expected ? <p>{person.rf_expected}.</p> : null}
                  <p className="text-muted-foreground">
                    Последний снимок перечня: {formatDate(person.snapshot_date)}. Дата включения — свойство записи перечня, а не
                    человека. {person.inclusion_attribution}
                  </p>
                </CardContent>
              </Card>
            </div>
            <h3 className="mb-1 font-medium">Ограничения</h3>
            <ul className="list-disc pl-5 text-sm">
              {person.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Section>

          <Section id="charges" title="Статьи УК">
            {person.charges.length ? (
              <ul className="space-y-1 text-sm">
                {person.charges.map((charge) => (
                  <li key={charge.article}>
                    <Link className="underline" to={`/entities?article=${encodeURIComponent(charge.article)}&role=all`}>
                      ст. {charge.article}
                    </Link>
                    {charge.parts.length ? ` (${charge.parts.map((part) => `ч. ${part}`).join(", ")})` : ""}{" "}
                    {charge.political ? <Badge variant="outline">политическая</Badge> : null}{" "}
                    {charge.shared ? <Badge variant="outline">общая</Badge> : null}{" "}
                    <span className="text-muted-foreground">публикаций: {charge.publications}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Ни одно событие не называет статью УК рядом с этим человеком.</p>
            )}
          </Section>

          <Section id="timeline" title="Хронология">
            {person.timeline.length ? (
              <ol className="space-y-4">
                {person.timeline.map((item) => (
                  <li key={`${item.event_type}-${item.day ?? "none"}`} className="border-l-2 pl-4">
                    <p className="text-sm text-muted-foreground">
                      {item.dated ? `${formatDate(item.day)}, по дате публикации` : `дата не установлена; публикация ${formatDate(item.day)}`}
                    </p>
                    <p className="text-sm">
                      <b>{item.label}</b>
                      {item.articles.length ? ` · ст. ${item.articles.join(", ")}` : ""}
                      <span className="text-muted-foreground">
                        {" "}
                        · источников: {item.sources.length}
                        {item.confidence !== null && item.confidence !== undefined ? ` · достоверность ${item.confidence.toFixed(2)}` : ""} ·
                        извлечено: {item.extractor_label}
                      </span>
                    </p>
                    {item.orgs.length ? (
                      <p className="text-sm">{item.orgs.map((org) => `${org.name} (${org.role_label})`).join(", ")}</p>
                    ) : null}
                    <ul className="mt-1 space-y-2">
                      {item.sources.map((source) => (
                        <li key={source.article_id}>
                          <Link className="text-sm underline" to={articleHref(source)}>
                            {source.title}
                          </Link>{" "}
                          <span className="text-xs text-muted-foreground">
                            — {source.source}, {formatDate(source.published_at)}
                          </span>
                          <Excerpt quote={source.quote} start={source.start} end={source.end} />
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground">Нет событий, где этот человек назван участником.</p>
            )}
            {person.timeline_capped ? <p className="mt-2 text-sm text-muted-foreground">Показаны первые {person.timeline_limit} событий.</p> : null}
          </Section>

          <Section id="evidence" title="Доказательства">
            {person.publications.length ? (
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  {person.article_count > person.publications.length
                    ? `Последние ${person.publications.length} из ${person.article_count}.`
                    : `Все публикации: ${person.publications.length}.`}
                </p>
                {person.publications.map((publication) => {
                  const href = externalUrl(publication.url);
                  return (
                    <Card key={publication.article_id}>
                      <CardContent className="space-y-1 text-sm">
                        <Link className="font-medium underline" to={articleHref(publication)}>
                          {publication.title}
                        </Link>
                        <p className="text-xs text-muted-foreground">
                          {publication.source} · {formatDate(publication.published_at)}
                          {href ? (
                            <>
                              {" · "}
                              <a className="underline" href={href} rel="noopener noreferrer" target="_blank">
                                источник
                              </a>
                            </>
                          ) : null}
                        </p>
                        {publication.identification ? <p className="text-muted-foreground">{publication.identification}</p> : null}
                        <Excerpt quote={publication.quote} start={publication.start} end={publication.end} />
                        <Facts
                          rows={[
                            ...(publication.events.length ? [["События", publication.events.join(", ")] as [string, ReactNode]] : []),
                            ...(publication.articles.length ? [["Статьи УК", publication.articles.join(", ")] as [string, ReactNode]] : []),
                            ...(publication.others.length
                              ? [
                                  [
                                    "Другие люди",
                                    publication.others.map((other, index) => (
                                      <span key={other.key}>
                                        {index ? ", " : ""}
                                        <Link className="underline" to={dossierPath(other.key)}>
                                          {other.name}
                                        </Link>
                                      </span>
                                    ))
                                  ] as [string, ReactNode]
                                ]
                              : [])
                          ]}
                        />
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Публикаций нет.</p>
            )}
          </Section>

          <Section id="graph" title="Граф событий">
            <p className="mb-2 text-sm text-muted-foreground">
              Человек связан с событием, в котором он назван; событие — со своими публикациями, судом, органом и статьёй. Двое
              связаны только через событие, где названы оба. «Назван в событии» — не «обвиняемый». «Совместные упоминания» — счёт
              общих публикаций, а не установленная связь.
            </p>
            {/* Keyed by the person: another dossier is another graph, never this one redrawn. */}
            <EventGraph key={person.key} graphUrl={person.graph_url} name={person.name} />
          </Section>

          {person.related.length ? (
            <Section id="related" title="Люди из тех же публикаций">
              <p className="mb-2 text-sm text-muted-foreground">Совместные упоминания: счёт общих публикаций, не установленная связь.</p>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Человек</TableHead>
                    <TableHead>Общих публикаций</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {person.related.map((other) => (
                    <TableRow key={other.key}>
                      <TableCell>
                        <Link className="underline" to={dossierPath(other.key)}>
                          {other.name}
                        </Link>
                      </TableCell>
                      <TableCell>{formatNumber(other.shared)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Section>
          ) : null}
        </>
      )}
    </QueryState>
  );
}
