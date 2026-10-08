import { useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getAskV1, postAskV1, type AskAnswerResponse } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatDateTime } from "@/lib/format";

type Row = Record<string, unknown>;

// «[№ 123]»; a model may drop the sign, so a bare number of an id's length is one too.
const REFERENCE = /\[(?:№\s*(\d+)|(\d{3,}))\]/g;

function number(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  return typeof value === "number" && !Number.isInteger(value) ? String(value).replace(".", ",") : String(value);
}

/** The answer by paragraphs; «[№ 123]» links to the publication. */
function Answer({ text }: { text: string }) {
  return (
    <>
      {text
        .split("\n")
        .filter((part) => part.trim())
        .map((part, index) => {
          const pieces: ReactNode[] = [];
          let last = 0;
          for (const found of part.matchAll(REFERENCE)) {
            const article = found[1] ?? found[2];
            pieces.push(part.slice(last, found.index));
            pieces.push(
              <Link key={found.index} className="underline" to={`/articles/${article}`}>
                [№ {article}]
              </Link>
            );
            last = (found.index ?? 0) + found[0].length;
          }
          pieces.push(part.slice(last));
          return <p key={index}>{pieces}</p>;
        })}
    </>
  );
}

const STATS: [string, string][] = [
  ["name", "Группа"],
  ["cases", "Дел"],
  ["imprisoned", "Лишение свободы"],
  ["mean_years", "Средний срок, лет"],
  ["median_years", "Медиана, лет"],
  ["max_years", "Наибольший, лет"],
  ["suspended", "Условно"],
  ["fined", "Штраф"],
  ["in_absentia", "Заочно"]
];

function Stats({ result }: { result: Row }) {
  const groups = (result.groups as Row[]) ?? [];
  const small = (result.small_groups as Row[]) ?? [];
  const notes = [
    result.group_unknown ? `без этой группы (не названа в публикации): ${result.group_unknown}` : "",
    result.year_unknown ? `год приговора неизвестен, не вошли: ${result.year_unknown}` : "",
    result.small_groups_left_out ? `групп отброшено как слишком маленькие: ${result.small_groups_left_out}` : ""
  ].filter(Boolean);
  const line = (group: Row, key: number | string) => (
    <TableRow key={key}>
      {STATS.map(([name]) => (
        <TableCell key={name}>{number(group[name])}</TableCell>
      ))}
    </TableRow>
  );
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            {STATS.map(([name, label]) => (
              <TableHead key={name}>{label}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map(line)}
          {small.length ? (
            <TableRow>
              <TableHead colSpan={STATS.length}>Слишком мало сроков для сравнения</TableHead>
            </TableRow>
          ) : null}
          {small.map((group, index) => line(group, `small-${index}`))}
        </TableBody>
        <TableFooter>{line((result.total as Row) ?? {}, "total")}</TableFooter>
      </Table>
      {notes.length ? <p className="text-sm text-muted-foreground">Дел {notes.join("; ")}.</p> : null}
    </>
  );
}

function Cases({ result }: { result: Row }) {
  const cases = (result.cases as Row[]) ?? [];
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            {["Человек", "Регион", "Наказание", "Срок", "Дата", "За что", "Публикация"].map((name) => (
              <TableHead key={name}>{name}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {cases.map((item, index) => {
            const publications = Number(item.publications ?? 1);
            const term = item.years ? `${number(item.years)} лет` : "";
            const fine = item.fine_rub ? `, штраф ${Number(item.fine_rub).toLocaleString("ru-RU")} ₽` : "";
            return (
              <TableRow key={index}>
                <TableCell className="whitespace-normal">{String(item.person)}</TableCell>
                <TableCell className="whitespace-normal">{String(item.region || DASH)}</TableCell>
                <TableCell className="whitespace-normal">
                  {String(item.punishment)}
                  {item.in_absentia ? " (заочно)" : ""}
                </TableCell>
                <TableCell>{term + fine || DASH}</TableCell>
                <TableCell>{String(item.sentenced_on || DASH)}</TableCell>
                <TableCell className="whitespace-normal">
                  {String(item.reason)}
                  <div className="text-xs text-muted-foreground">{String(item.charge)}</div>
                </TableCell>
                <TableCell className="whitespace-normal">
                  <Link className="underline" to={`/articles/${item.article_id}`}>
                    {String(item.title)}
                  </Link>
                  <div className="text-xs text-muted-foreground">
                    {String(item.source)}
                    {publications > 1 ? ` и ещё ${publications - 1}` : ""}
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <p className="text-sm text-muted-foreground">
        Показано {cases.length} из {number(result.total ?? cases.length)}.
      </p>
    </>
  );
}

function Found({ result, open, close }: { result: Row; open: string; close: string }) {
  const found = (result.publications as Row[]) ?? [];
  if (!found.length) {
    return <p className="text-sm text-muted-foreground">Ничего не найдено.</p>;
  }
  // The words found stand between the marks.
  const snippet = (text: string) =>
    text.split(open).map((part, index) => {
      if (!index) {
        return part;
      }
      const [marked, rest = ""] = part.split(close);
      return (
        <span key={index}>
          <mark>{marked}</mark>
          {rest}
        </span>
      );
    });
  return (
    <>
      <ul className="space-y-2 text-sm">
        {found.map((item) => (
          <li key={String(item.article_id)}>
            <Link className="underline" to={`/articles/${item.article_id}`}>
              {String(item.title)}
            </Link>{" "}
            <span className="text-muted-foreground">
              [№ {String(item.article_id)}] {String(item.source)}, {String(item.published || "без даты")}
            </span>
            <div>{snippet(String(item.snippet ?? ""))}</div>
          </li>
        ))}
      </ul>
      <p className="text-sm text-muted-foreground">
        Показано {found.length} из {number(result.total ?? found.length)}.
      </p>
    </>
  );
}

function Shown({ answer, open, close }: { answer: AskAnswerResponse; open: string; close: string }) {
  const failed = answer.outcome === "failed";
  return (
    <section id="answer" aria-label="Ответ" className={`space-y-3 rounded-lg border bg-card p-4 ${failed ? "border-destructive" : ""}`}>
      <h2 className="text-lg font-semibold">{answer.question}</h2>
      {answer.unverified.length ? (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Этих чисел нет в подсчётах под ответом, модель вывела их сама — проверьте по таблицам: {answer.unverified.join(", ")}.
        </p>
      ) : null}
      <div className="space-y-2">
        <Answer text={answer.answer} />
      </div>
      {answer.results.length ? (
        <details open className="space-y-3">
          <summary className="cursor-pointer font-medium">На чём основан ответ</summary>
          {answer.results.map((result, index) => (
            <div key={index} className="space-y-2 pt-2">
              <h3 className="font-medium">{String(result.what ?? "")}</h3>
              {result.tool === "list" ? <Cases result={result} /> : result.tool === "search" ? <Found result={result} open={open} close={close} /> : <Stats result={result} />}
            </div>
          ))}
        </details>
      ) : null}
      <p className="text-sm text-muted-foreground">
        {formatDateTime(answer.asked_at)} · {answer.model} · ${answer.cost_usd.toFixed(4)}
      </p>
    </section>
  );
}

export function AskPage() {
  const url = useUrlState();
  const chosen = url.getNumber("q", 0);
  const query = chosen > 0 ? { q: chosen } : {};
  const page = useQuery({ queryKey: ["ask", query], queryFn: () => unwrap(getAskV1({ query })) });
  const [question, setQuestion] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const client = useQueryClient();
  const asking = useMutation({
    mutationFn: (text: string) => unwrap(postAskV1({ body: { question: text } })),
    onSuccess: async (asked) => {
      setNote(asked.note ?? null);
      if (asked.id !== null && asked.id !== undefined) {
        setQuestion("");
        url.set({ q: asked.id });
      }
      await client.invalidateQueries({ queryKey: ["ask"] });
    }
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setNote(null);
    asking.mutate(question);
  }

  return (
    <>
      <PageHeader
        title="Спросить"
        instruction="Вопрос обычными словами. Система считает по приговорам, выписанным из публикаций, или ищет по текстам и пишет ответ по результатам. Сверяйте числа с таблицами под ответом."
      />
      <QueryState query={page}>
        {(data) => (
          <div className="space-y-4">
            <form onSubmit={submit} className="space-y-2 rounded-lg border bg-card p-4">
              <label htmlFor="ask-question" className="text-sm font-medium">
                Вопрос
              </label>
              <textarea
                id="ask-question"
                rows={3}
                required
                maxLength={data.max_question}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                placeholder="Например: в каких регионах самые суровые наказания за антивоенные высказывания?"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
              />
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" disabled={asking.isPending || !question.trim()}>
                  {asking.isPending ? "Считаю…" : "Спросить"}
                </Button>
                <span className="text-sm text-muted-foreground">
                  Сегодня потрачено ${data.spent_today.toFixed(2)} из ${data.budget.toFixed(2)}. Ответ занимает до 20 секунд.
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {data.examples.map((example) => (
                  <button key={example} type="button" className="rounded-full border px-3 py-1 text-xs hover:bg-muted" onClick={() => setQuestion(example)}>
                    {example}
                  </button>
                ))}
              </div>
            </form>
            {note ? <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">{note}</p> : null}
            {asking.isError ? <p className="text-sm text-destructive">{asking.error.message}</p> : null}
            {data.answer ? <Shown answer={data.answer} open={data.mark_open} close={data.mark_close} /> : <p className="text-sm text-muted-foreground">Вопросов ещё не было.</p>}
            {data.history.length ? (
              <section className="rounded-lg border bg-card p-4">
                <h2 className="mb-2 font-medium">Прежние вопросы</h2>
                <ul className="space-y-1 text-sm">
                  {data.history.map((one) => (
                    <li key={one.id}>
                      <button type="button" className="text-left underline" onClick={() => url.set({ q: one.id })}>
                        {one.question}
                      </button>{" "}
                      <span className="text-muted-foreground">{formatDateTime(one.asked_at)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        )}
      </QueryState>
    </>
  );
}
