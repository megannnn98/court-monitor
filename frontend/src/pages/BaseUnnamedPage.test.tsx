import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { decideBaseUnnamedV1, listBaseUnnamedV1 } from "@/api/generated";
import { BaseUnnamedPage } from "@/pages/BaseUnnamedPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listBaseUnnamedV1: vi.fn(), decideBaseUnnamedV1: vi.fn() }));
const list = vi.mocked(listBaseUnnamedV1);
const decide = vi.mocked(decideBaseUnnamedV1);

const KEY = "будников евгений|1975-10-09";
const CARD = {
  record: "share:known:1",
  full_name: "50-летний житель Рубцовска",
  facts: "50 лет · мужчина · Алтайский край, Рубцовск · ст. 280.3",
  state: "open",
  identified_as: null,
  confirmed: null,
  candidates: [
    { key: KEY, full_name: "БУДНИКОВ ЕВГЕНИЙ", birth_date: "1975-10-09", birth_place: "Г. РУБЦОВСК", reasons: ["родился в Рубцовске"], decision: null }
  ],
  total: 1,
  courts: [],
  courts_total: 0
};
const PAGE = {
  statuses: [
    { value: "open", label: "Не разобраны", count: 1 },
    { value: "found", label: "Опознаны", count: 0 },
    { value: "all", label: "Все", count: 1 }
  ],
  items: [CARD],
  total: 1,
  page: 1,
  page_size: 20,
  none_yet: false
};

beforeEach(() => {
  list.mockReset();
  decide.mockReset();
  list.mockReturnValue(ok(PAGE) as never);
});

it("shows a record and its candidates, and says «Это он»", async () => {
  decide.mockReturnValue(ok({ record: "share:known:1" }) as never);
  renderPage(<BaseUnnamedPage />);

  const card = await screen.findByRole("article", { name: CARD.full_name });
  expect(within(card).getByText(CARD.facts)).toBeTruthy();
  expect(within(card).getByText("не разобран")).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", { name: "Это он" }));

  await waitFor(() => expect(decide).toHaveBeenCalledWith({ body: { record: "share:known:1", candidate: KEY, decision: "same" } }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  expect(list).toHaveBeenCalledWith({ query: { status: "open", page: 1 } });
});

it("takes back a word on an entry no longer among the candidates", async () => {
  decide.mockReturnValue(failed(400, "Неполное решение") as never);
  list.mockReturnValue(ok({ ...PAGE, items: [{ ...CARD, state: "confirmed", identified_as: "ушедший иван", confirmed: "ушедший иван|1975-01-01", candidates: [] }] }) as never);
  renderPage(<BaseUnnamedPage />);

  const card = await screen.findByRole("article", { name: CARD.full_name });
  expect(within(card).getByText("этой записи сейчас нет среди кандидатов из перечня")).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", { name: "Отменить решение" }));

  await waitFor(() => expect(decide).toHaveBeenCalledWith({ body: { record: "share:known:1", candidate: "ушедший иван|1975-01-01", decision: "clear" } }));
  expect(await within(card).findByText("Неполное решение")).toBeTruthy();
});

it("opens another tab", async () => {
  renderPage(<BaseUnnamedPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Все 1" }));

  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { status: "all", page: 1 } }));
});
