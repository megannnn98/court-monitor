import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  chooseEntityModelV1,
  getEntityModelV1,
  refreshCatalogV1,
  testEntityModelV1,
  type EntityModelOptionResponse,
  type EntityModelSettingsResponse,
  type EntityModelSpendLineResponse,
  type EntityModelTestResponse
} from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { unwrap } from "@/lib/api";

const QUERY_KEY = ["entity-model"];

/** «$0.04», four places under a cent; «неизвестна» when the catalog gave none. */
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "неизвестна";
  }
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

/** What was spent: four places, so that half a cent is not «$0.00» (as `spend.money`). */
export function spent(value: number | null | undefined): string {
  return value === null || value === undefined ? "неизвестно" : `$${value.toFixed(4)}`;
}

function Structured({ value }: { value: boolean | null }) {
  if (value === null) {
    return <span className="text-muted-foreground">неизвестно</span>;
  }
  return value ? <span>да</span> : <span className="text-destructive">нет</span>;
}

function ModelDetails({ option, tested }: { option: EntityModelOptionResponse; tested: string | null }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      <dt className="text-muted-foreground">Model ID</dt>
      <dd>
        <code>{option.id}</code> {option.free ? <Badge>FREE</Badge> : null}
      </dd>
      <dt className="text-muted-foreground">Вход, за 1 млн токенов</dt>
      <dd>{usd(option.prompt_usd_per_million)}</dd>
      <dt className="text-muted-foreground">Выход, за 1 млн токенов</dt>
      <dd>{usd(option.completion_usd_per_million)}</dd>
      <dt className="text-muted-foreground">Structured output / JSON Schema</dt>
      <dd>
        <Structured value={option.structured_outputs} />
      </dd>
      <dt className="text-muted-foreground">В каталоге OpenRouter</dt>
      <dd>{option.listed === null ? "неизвестно" : option.listed ? "да" : <span className="text-destructive">нет — сейчас недоступна</span>}</dd>
      <dt className="text-muted-foreground">Тест пройден</dt>
      <dd>{tested ? new Date(tested).toLocaleString("ru-RU") : "нет"}</dd>
      {option.price_stale ? (
        <>
          <dt className="text-muted-foreground">Цена</dt>
          <dd className="text-amber-700">{option.price_text}</dd>
        </>
      ) : null}
    </dl>
  );
}

function TestResult({ result }: { result: EntityModelTestResponse }) {
  return (
    <div className="space-y-2 rounded-md border p-3 text-sm" role="status" aria-label="Результат теста">
      <p className={result.ok ? "font-medium text-emerald-700" : "font-medium text-destructive"}>
        {result.ok ? "Успешно" : "Ошибка"}: {result.model}
      </p>
      {result.error ? <p className="text-destructive">{result.error}</p> : null}
      <p>
        Время ответа: {result.seconds.toFixed(2)} с · токены: вход {result.prompt_tokens}, выход {result.completion_tokens} · стоимость:{" "}
        {result.cost_usd !== null && result.cost_usd !== undefined
          ? usd(result.cost_usd)
          : result.estimated_cost_usd !== null && result.estimated_cost_usd !== undefined
            ? `${usd(result.estimated_cost_usd)} (оценка по тарифу)`
            : "неизвестна"}
      </p>
      {result.entities.length ? (
        <>
          <p>
            Найденные сущности (совпало с ожидаемым {result.expected_matched} из {result.expected_total}):
          </p>
          <ul className="list-disc pl-5">
            {result.entities.map((entity) => (
              <li key={entity.source}>
                «{entity.source}» →{" "}
                {entity.role ? `${entity.role}: ${entity.note}` : entity.is_person ? `${entity.nominative} (${entity.gender})` : "не человек"}
                {entity.as_expected ? "" : " — не как ожидалось"}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function SpendTable({ title, lines }: { title: string; lines: EntityModelSpendLineResponse[] }) {
  const reported = lines.reduce((sum, line) => sum + line.cost_usd, 0);
  const estimated = lines.reduce((sum, line) => sum + (line.estimated_usd ?? 0), 0);
  const unknown = lines.some((line) => line.estimated_usd === null);
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">
        {title}: {spent(reported)}
        {estimated > 0 ? ` + ${spent(estimated)} (оценка)` : ""}
        {unknown ? " + часть неизвестна" : ""}
      </h3>
      {lines.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Модель</TableHead>
              <TableHead className="text-right">Запросов</TableHead>
              <TableHead className="text-right">Вход, токенов</TableHead>
              <TableHead className="text-right">Выход, токенов</TableHead>
              <TableHead className="text-right">Стоимость</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {lines.map((line) => (
              <TableRow key={line.model}>
                <TableCell>
                  <code>{line.model}</code>
                </TableCell>
                <TableCell className="text-right">{line.calls}</TableCell>
                <TableCell className="text-right">{line.prompt_tokens}</TableCell>
                <TableCell className="text-right">{line.completion_tokens}</TableCell>
                <TableCell className="text-right">
                  {spent(line.cost_usd)}
                  {line.uncosted_calls ? ` + ${line.estimated_usd === null ? "неизвестно" : `${spent(line.estimated_usd)} оценка`}` : ""}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm text-muted-foreground">Расходов нет.</p>
      )}
    </div>
  );
}

type Kind = "extraction" | "decision";

const KINDS: Record<Kind, { title: string; steps: string }> = {
  extraction: {
    title: "Модель извлечения сущностей",
    steps: "имена людей (шаг «Собрать сущности»), безымянные фигуранты, приговоры и дайджесты"
  },
  decision: {
    title: "Модель решений",
    steps: "кто обвиняемый (шаг «Найти фигурантов»), политическое ли дело и что за новость по делу"
  }
};

function Chooser({ data, kind }: { data: EntityModelSettingsResponse; kind: Kind }) {
  const client = useQueryClient();
  const current = kind === "decision" ? data.decision_current : data.current;
  const source = kind === "decision" ? data.decision_source : data.source;
  const [selected, setSelected] = useState(current);
  const [result, setResult] = useState<EntityModelTestResponse | null>(null);
  useEffect(() => setSelected(current), [current]);
  const option = data.models.find((model) => model.id === selected) ?? data.models[0];
  const tested = kind === "decision" ? option?.decision_tested_at : option?.tested_at;
  const update = (fresh: EntityModelSettingsResponse) => client.setQueryData(QUERY_KEY, fresh);

  const test = useMutation({
    mutationFn: () => unwrap(testEntityModelV1({ body: { model: selected, kind } })),
    onSuccess: async (fresh) => {
      setResult(fresh);
      await client.invalidateQueries({ queryKey: QUERY_KEY });
    }
  });
  const save = useMutation({ mutationFn: () => unwrap(chooseEntityModelV1({ body: { model: selected, kind } })), onSuccess: update });
  const failure = test.error ?? save.error;
  const needsTest = selected !== data.default && !tested;
  const id = `${kind}-model-select`;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{KINDS[kind].title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>
          Сейчас: <code>{current}</code>
          {source === "environment" ? " (не выбрана в консоли: модель из окружения)" : ""}. Отвечает за: {KINDS[kind].steps}. Применяется к новым запускам; уже
          идущий запуск сохраняет свою модель. Отсев мусора (JEV) не меняется.
        </p>
        {kind === "decision" ? (
          <p className="text-muted-foreground">
            Тест модели решений — задача шага фигурантов, три прогона подряд: комментатор и человек, задержанный за границей, рядом с чужим российским делом. Модель, которая хоть раз сделает кого-то из них обвиняемым или потеряет настоящего обвиняемого, тест не пройдёт. Редкую случайную ошибку тест может пропустить; политичность дела он пока не проверяет.
          </p>
        ) : null}
        {!data.openrouter_configured ? <p className="text-destructive">OPENROUTER_API_KEY не задан: шаги модель не спрашивают, тест невозможен.</p> : null}
        <div className="space-y-1">
          <Label htmlFor={id}>{kind === "decision" ? "Модель решений" : "Модель"}</Label>
          <select
            id={id}
            className="h-9 w-full max-w-xl rounded-md border bg-background px-2"
            value={selected}
            onChange={(event) => {
              setSelected(event.target.value);
              setResult(null);
            }}
          >
            {data.models.map((model) => (
              <option key={model.id} value={model.id} disabled={!model.usable}>
                {model.label} — {model.id} — {model.free ? "FREE" : `${usd(model.prompt_usd_per_million)} / ${usd(model.completion_usd_per_million)}`}
                {model.usable ? "" : " (недоступна)"}
              </option>
            ))}
          </select>
        </div>
        {option ? <ModelDetails option={option} tested={tested ?? null} /> : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" disabled={test.isPending || !data.openrouter_configured || !option?.usable} onClick={() => test.mutate()}>
            {test.isPending ? "Тест идёт…" : "Тест"}
          </Button>
          <Button disabled={save.isPending || selected === current || needsTest || !option?.usable} onClick={() => save.mutate()}>
            Сохранить выбор
          </Button>
          {needsTest ? <span className="text-muted-foreground">Сохранить можно после успешного теста.</span> : null}
        </div>
        {result ? <TestResult result={result} /> : null}
        {failure ? <p className="text-destructive">{failure.message}</p> : null}
      </CardContent>
    </Card>
  );
}

function Settings({ data }: { data: EntityModelSettingsResponse }) {
  const client = useQueryClient();
  const update = (fresh: EntityModelSettingsResponse) => client.setQueryData(QUERY_KEY, fresh);
  const refresh = useMutation({ mutationFn: () => unwrap(refreshCatalogV1()), onSuccess: update });

  return (
    <div className="space-y-4">
      <Chooser data={data} kind="extraction" />
      <Chooser data={data} kind="decision" />
      <Card>
        <CardHeader>
          <CardTitle>Цены OpenRouter</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            {data.catalog_fetched_at
              ? `Каталог прочитан ${new Date(data.catalog_fetched_at).toLocaleString("ru-RU")}${data.catalog_stale ? " — последнее обновление не удалось, цены устарели" : ""}.`
              : "Каталог OpenRouter сейчас недоступен: цены неизвестны."}
            {data.catalog_error ? ` (${data.catalog_error})` : ""}
          </p>
          <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            Обновить цены
          </Button>
          {refresh.error ? <p className="text-destructive">{refresh.error.message}</p> : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Расходы на модели извлечения и решений</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">Фактическая стоимость от OpenRouter; без неё — оценка по тарифу. Расходы JEV сюда не входят.</p>
          <SpendTable title="Сегодня (UTC)" lines={data.spend_today} />
          <SpendTable title="За всё время" lines={data.spend_total} />
        </CardContent>
      </Card>
    </div>
  );
}

/** «Модель извлечения»: which OpenRouter model the entity steps ask, at what price; a real test; what it spent. */
export function EntityModelPage() {
  const query = useQuery({ queryKey: QUERY_KEY, queryFn: () => unwrap(getEntityModelV1()) });
  return (
    <>
      <PageHeader title="Модель извлечения" instruction="Выберите модель, нажмите «Тест», затем сохраните выбор." />
      <QueryState query={query}>{(data) => <Settings data={data} />}</QueryState>
    </>
  );
}
