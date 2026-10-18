import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getCycleV1, startCycleV1, stopCycleRunV1 } from "@/api/generated";
import { WorkPage } from "@/pages/WorkPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getCycleV1: vi.fn(), startCycleV1: vi.fn(), stopCycleRunV1: vi.fn() }));
const cycle = vi.mocked(getCycleV1);
const start = vi.mocked(startCycleV1);
const stop = vi.mocked(stopCycleRunV1);

const STEPS = [
  { stage: "load", number: 1, label: "Публикации загружены", status: "ready" },
  { stage: "purge", number: 2, label: "Публикации проверены", status: "waiting" }
];
const IDLE = {
  attention: { key: "pairs", title: "Спорные пары", count: 4, href: "/ui/pairs", description: "Решите, один ли это человек." },
  tasks: [{ key: "unnamed", title: "Безымянные", count: 2, href: "/ui/unnamed", description: "" }],
  steps: STEPS,
  current_stage: "load",
  live: null,
  latest_run_id: 41,
  chain_span: "шаги 1–5 подряд",
  chain_question: "С баланса OpenRouter спишутся деньги: примерно $0.07 за один день новостей. Остаток: $9.16. Запустить?",
  chain_note: "",
  chain_stopped: ""
};

beforeEach(() => {
  for (const mock of [cycle, start, stop]) {
    mock.mockReset();
  }
});

it("sends a queue to its React page", async () => {
  cycle.mockReturnValue(ok(IDLE) as never);

  renderPage(<WorkPage />);

  expect((await screen.findByRole("link", { name: "Начать проверку" })).getAttribute("href")).toBe("/review/pairs");
  expect(screen.getByRole("link", { name: /Безымянные/ }).getAttribute("href")).toBe("/unnamed");
  expect(screen.getByText("Публикации загружены")).toBeTruthy();
});

it("asks before «Сделать всё» and names the latest run it was read for", async () => {
  cycle.mockReturnValue(ok(IDLE) as never);
  start.mockReturnValue(ok({ run_id: 42, stage: "load" }) as never);

  renderPage(<WorkPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Сделать всё" }));

  expect(await screen.findByText(/спишутся деньги/)).toBeTruthy();
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ body: { after: 41, entity_model: null, decision_model: null } }));
});

it("stops a live run and shows a refusal", async () => {
  cycle.mockReturnValue(
    ok({ ...IDLE, attention: null, live: { run_id: 42, title: "Подгрузить статьи" }, chain_note: "Идёт «Сделать всё»" }) as never
  );
  stop.mockReturnValue(failed(404, "Запуск не найден") as never);

  renderPage(<WorkPage />);
  expect(await screen.findByText("Идёт автоматическая обработка")).toBeTruthy();
  expect(screen.getByRole("link", { name: /Ход запуска #42/ }).getAttribute("href")).toBe("/logs?run_id=42");
  fireEvent.click(screen.getByRole("button", { name: "Остановить" }));
  fireEvent.click(await screen.findByRole("button", { name: "Остановить" }));

  await waitFor(() => expect(stop).toHaveBeenCalledWith({ path: { run_id: 42 } }));
  expect(await screen.findByText("Запуск не найден")).toBeTruthy();
});

const NOTICE = {
  model: "qwen/qwen3.8-flash",
  label: "Qwen 3.8 Flash",
  free: false,
  prompt_usd_per_million: 0.15,
  completion_usd_per_million: 0.47,
  price_stale: false,
  kind: "платная",
  price_text: "Вход $0.15 / 1 млн токенов, выход $0.47 / 1 млн токенов. Платная."
};

it("shows the entity model and its price before «Запустить» and starts with that model", async () => {
  cycle.mockReturnValue(ok({ ...IDLE, entity_model: NOTICE }) as never);
  start.mockReturnValue(ok({ run_id: 42, stage: "load" }) as never);
  renderPage(<WorkPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Сделать всё" }));

  const notice = screen.getByLabelText("Модель извлечения сущностей");
  expect(notice.textContent).toContain("qwen/qwen3.8-flash");
  expect(notice.textContent).toContain("Входные токены: $0.15 / 1 млн");
  expect(notice.textContent).toContain("Выходные токены: $0.47 / 1 млн");
  expect(notice.textContent).toContain("Тип: платная");
  fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ body: { after: 41, entity_model: "qwen/qwen3.8-flash", decision_model: null } }));
});

it("says FREE and $0 for a free model, and that an unknown price is unknown", async () => {
  cycle.mockReturnValue(
    ok({ ...IDLE, entity_model: { ...NOTICE, model: "nvidia/nemotron-3.5-super-vl:free", free: true, prompt_usd_per_million: 0, completion_usd_per_million: 0, kind: "бесплатная" } }) as never
  );
  const { unmount } = renderPage(<WorkPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Сделать всё" }));
  const free = screen.getByLabelText("Модель извлечения сущностей").textContent ?? "";
  expect(free).toContain("FREE");
  expect(free).toContain("Стоимость: $0");
  unmount();

  cycle.mockReturnValue(
    ok({ ...IDLE, entity_model: { ...NOTICE, prompt_usd_per_million: null, completion_usd_per_million: null, kind: "цена неизвестна" } }) as never
  );
  renderPage(<WorkPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Сделать всё" }));
  const unknown = screen.getByLabelText("Модель извлечения сущностей").textContent ?? "";
  expect(unknown).toContain("Текущие цены неизвестны");
  expect(unknown).not.toContain("$0");
});

it("shows the decision model apart and starts with both", async () => {
  cycle.mockReturnValue(
    ok({ ...IDLE, entity_model: { ...NOTICE, model: "nvidia/nemotron-3.5-super-vl:free", free: true, kind: "бесплатная" }, decision_model: NOTICE }) as never
  );
  start.mockReturnValue(ok({ run_id: 42, stage: "load" }) as never);
  renderPage(<WorkPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Сделать всё" }));

  expect(screen.getByLabelText("Модель извлечения сущностей").textContent).toContain("FREE");
  expect(screen.getByLabelText("Модель решений (фигуранты, политичность)").textContent).toContain("qwen/qwen3.8-flash");
  fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
  await waitFor(() =>
    expect(start).toHaveBeenCalledWith({
      body: { after: 41, entity_model: "nvidia/nemotron-3.5-super-vl:free", decision_model: "qwen/qwen3.8-flash" }
    })
  );
});

const DATED = {
  ...IDLE,
  working_date: "2026-09-20",
  period_question: "Выполнить шаги 1–5 подряд?",
  last_load: { finished_at: "2026-10-17T07:36:00Z", published_from: null, published_to: null }
};

it("says what a press loads without dates, the working date and the latest load", async () => {
  cycle.mockReturnValue(ok(DATED) as never);

  renderPage(<WorkPage />);

  expect(await screen.findByText(/Без дат — догрузить всё новое с прошлой загрузки/)).toBeTruthy();
  expect(screen.getByText(/Рабочая дата 20\.09\.2026/)).toBeTruthy();
  expect(screen.getByText(/Последняя загрузка: .*17\.10\.2026.*без дат/)).toBeTruthy();
  expect(screen.getByText(/Telegram — не глубже 30 дней/)).toBeTruthy();
  const from = screen.getByLabelText("Скачать с");
  expect(from.getAttribute("min")).toBe("2026-09-20");
});

it("loads a period from a date to now: steps 1–5, and says so before it starts", async () => {
  cycle.mockReturnValue(ok(DATED) as never);
  start.mockReturnValue(ok({ run_id: 42, stage: "load" }) as never);

  renderPage(<WorkPage />);
  fireEvent.change(await screen.findByLabelText("Скачать с"), { target: { value: "2026-10-01" } });
  expect(screen.getByText(/Шаги 1–5: загрузить опубликованное с 01\.10\.2026 по настоящее время/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Сделать всё" }));

  expect(await screen.findByText(/Загрузить публикации с 01\.10\.2026 по настоящее время, затем обработать\. Выполнить шаги 1–5 подряд\?/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
  await waitFor(() =>
    expect(start).toHaveBeenCalledWith({
      body: { after: 41, entity_model: null, decision_model: null, published_from: "2026-10-01" }
    })
  );
});

it("loads a period between two dates", async () => {
  cycle.mockReturnValue(ok({ ...DATED, last_load: { finished_at: "2026-10-17T07:36:00Z", published_from: "2026-10-01", published_to: "2026-10-10" } }) as never);
  start.mockReturnValue(ok({ run_id: 42, stage: "load" }) as never);

  renderPage(<WorkPage />);
  expect(await screen.findByText(/Последняя загрузка: .*за 01\.10\.2026–10\.10\.2026/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Скачать с"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("по"), { target: { value: "2026-10-10" } });
  fireEvent.click(screen.getByRole("button", { name: "Сделать всё" }));
  fireEvent.click(await screen.findByRole("button", { name: "Запустить" }));

  await waitFor(() =>
    expect(start).toHaveBeenCalledWith({
      body: { after: 41, entity_model: null, decision_model: null, published_from: "2026-10-01", published_to: "2026-10-10" }
    })
  );
});

it("past step 1 continues the round: «Продолжить», no dates", async () => {
  cycle.mockReturnValue(ok({ ...DATED, current_stage: "purge", chain_span: "шаги 2–5 подряд" }) as never);
  start.mockReturnValue(ok({ run_id: 42, stage: "purge" }) as never);

  renderPage(<WorkPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Продолжить" }));

  expect(screen.queryByLabelText("Скачать с")).toBeNull();
  expect(screen.queryByText(/Без дат/)).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Запустить" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ body: { after: 41, entity_model: null, decision_model: null } }));
});
