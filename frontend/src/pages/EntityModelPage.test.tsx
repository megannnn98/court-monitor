import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { chooseEntityModelV1, getEntityModelV1, refreshCatalogV1, testEntityModelV1 } from "@/api/generated";
import { EntityModelPage, usd } from "@/pages/EntityModelPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({
  getEntityModelV1: vi.fn(),
  chooseEntityModelV1: vi.fn(),
  testEntityModelV1: vi.fn(),
  refreshCatalogV1: vi.fn()
}));
const read = vi.mocked(getEntityModelV1);
const choose = vi.mocked(chooseEntityModelV1);
const test = vi.mocked(testEntityModelV1);

const OPTION = {
  listed: true,
  free: false,
  price_stale: false,
  structured_outputs: true,
  tested_at: null,
  usable: true
};
const SETTINGS = {
  current: "deepseek/deepseek-v4.1-flash",
  source: "environment",
  decision_current: "deepseek/deepseek-v4.1-flash",
  decision_source: "environment",
  default: "deepseek/deepseek-v4.1-flash",
  catalog_fetched_at: "2026-10-16T07:00:00Z",
  catalog_stale: false,
  catalog_error: null,
  openrouter_configured: true,
  models: [
    {
      ...OPTION,
      id: "deepseek/deepseek-v4.1-flash",
      label: "DeepSeek V4.1 Flash (текущая)",
      prompt_usd_per_million: 0.09,
      completion_usd_per_million: 0.18,
      price_text: "Вход $0.09"
    },
    {
      ...OPTION,
      id: "nvidia/nemotron-3.5-super-vl:free",
      label: "NVIDIA Nemotron 3.5 Super VL (free)",
      free: true,
      prompt_usd_per_million: 0,
      completion_usd_per_million: 0,
      price_text: "FREE"
    },
    {
      ...OPTION,
      id: "stealth/space-bunny-alpha",
      label: "Space Bunny Alpha",
      listed: false,
      structured_outputs: false,
      usable: false,
      prompt_usd_per_million: null,
      completion_usd_per_million: null,
      price_text: "Цена сейчас неизвестна"
    }
  ],
  spend_today: [],
  spend_total: [
    {
      model: "deepseek/deepseek-v4.1-flash",
      calls: 4,
      prompt_tokens: 4000,
      completion_tokens: 500,
      cost_usd: 0.0123,
      estimated_usd: 0,
      uncosted_calls: 0
    }
  ]
};
const PASSED = {
  ok: true,
  model: "nvidia/nemotron-3.5-super-vl:free",
  seconds: 1.42,
  prompt_tokens: 812,
  completion_tokens: 96,
  cost_usd: 0,
  estimated_cost_usd: null,
  entities: [{ source: "Александра Моора", nominative: "Александр Моор", gender: "male", is_person: true, as_expected: true }],
  expected_matched: 1,
  expected_total: 3,
  error: null
};

beforeEach(() => {
  for (const mock of [read, choose, test, vi.mocked(refreshCatalogV1)]) {
    mock.mockReset();
  }
});

it("lists the models with their ids and prices, FREE for a free one, and greys an unlisted one", async () => {
  read.mockReturnValue(ok(SETTINGS) as never);

  renderPage(<EntityModelPage />);

  const select = (await screen.findByLabelText("Модель")) as HTMLSelectElement;
  const options = Array.from(select.options);
  expect(options.map((option) => option.value)).toEqual(SETTINGS.models.map((model) => model.id));
  expect(options[0].textContent).toContain("$0.09 / $0.18");
  expect(options[1].textContent).toContain("FREE");
  expect(options[2].disabled).toBe(true);
  expect(screen.getByRole("heading", { name: "За всё время: $0.0123" })).toBeTruthy();
});

it("saves a model only after its test passed, and shows what the test found", async () => {
  read.mockReturnValue(ok(SETTINGS) as never);
  test.mockReturnValue(ok(PASSED) as never);
  choose.mockReturnValue(ok({ ...SETTINGS, current: PASSED.model, source: "stored" }) as never);
  renderPage(<EntityModelPage />);

  const select = await screen.findByLabelText("Модель");
  fireEvent.change(select, { target: { value: PASSED.model } });
  const save = screen.getAllByRole("button", { name: "Сохранить выбор" })[0] as HTMLButtonElement;
  expect(save.disabled).toBe(true);

  read.mockReturnValue(ok({ ...SETTINGS, models: SETTINGS.models.map((model) => (model.id === PASSED.model ? { ...model, tested_at: "2026-10-16T07:05:00Z" } : model)) }) as never);
  fireEvent.click(screen.getAllByRole("button", { name: "Тест" })[0]);

  const result = await screen.findByRole("status", { name: "Результат теста" });
  expect(result.textContent).toContain("Успешно");
  expect(result.textContent).toContain("вход 812, выход 96");
  expect(result.textContent).toContain("Александр Моор");
  expect(test).toHaveBeenCalledWith({ body: { model: PASSED.model, kind: "extraction" } });
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
  await waitFor(() => expect(choose).toHaveBeenCalledWith({ body: { model: PASSED.model, kind: "extraction" } }));
});

it("shows why a test failed", async () => {
  read.mockReturnValue(ok(SETTINGS) as never);
  test.mockReturnValue(
    ok({ ...PASSED, ok: false, entities: [], error: "OpenRouter ответил 429: лимит запросов." }) as never
  );
  renderPage(<EntityModelPage />);

  fireEvent.click((await screen.findAllByRole("button", { name: "Тест" }))[0]);

  const result = await screen.findByRole("status", { name: "Результат теста" });
  expect(result.textContent).toContain("Ошибка");
  expect(result.textContent).toContain("429");
});

it("says a second test is refused", async () => {
  read.mockReturnValue(ok(SETTINGS) as never);
  test.mockReturnValue(failed(409, "Уже идёт тест модели: дождитесь его результата.") as never);
  renderPage(<EntityModelPage />);

  fireEvent.click((await screen.findAllByRole("button", { name: "Тест" }))[0]);

  expect(await screen.findByText("Уже идёт тест модели: дождитесь его результата.")).toBeTruthy();
});

it("words an unknown price as unknown, never as zero", () => {
  expect(usd(null)).toBe("неизвестна");
  expect(usd(0)).toBe("$0.00");
  expect(usd(0.0021)).toBe("$0.0021");
});

it("tests and saves the decision model apart, by its own test", async () => {
  read.mockReturnValue(ok(SETTINGS) as never);
  test.mockReturnValue(
    ok({
      ...PASSED,
      kind: "decision",
      entities: [{ source: "Андрей Колесов", nominative: "", gender: "", is_person: true, as_expected: true, role: "other", note: "комментарий" }]
    }) as never
  );
  renderPage(<EntityModelPage />);

  const select = await screen.findByLabelText("Модель решений");
  fireEvent.change(select, { target: { value: PASSED.model } });
  fireEvent.click(screen.getAllByRole("button", { name: "Тест" })[1]);

  const result = await screen.findByRole("status", { name: "Результат теста" });
  expect(result.textContent).toContain("other: комментарий");
  expect(test).toHaveBeenCalledWith({ body: { model: PASSED.model, kind: "decision" } });
  // The extraction's pass is no licence for the decision model.
  expect((screen.getAllByRole("button", { name: "Сохранить выбор" })[1] as HTMLButtonElement).disabled).toBe(true);
});
