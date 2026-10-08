import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { addOfficialV1, addSuggestedOfficialV1, deactivateOfficialV1, getOfficialsV1 } from "@/api/generated";
import { OfficialsPage } from "@/pages/OfficialsPage";
import { ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getOfficialsV1: vi.fn(), addOfficialV1: vi.fn(), addSuggestedOfficialV1: vi.fn(), deactivateOfficialV1: vi.fn() }));
const get = vi.mocked(getOfficialsV1);
const add = vi.mocked(addOfficialV1);
const suggested = vi.mocked(addSuggestedOfficialV1);
const deactivate = vi.mocked(deactivateOfficialV1);

const LIST = {
  total: 1,
  rows: [{ external_id: "console:ольга минакова", full_name: "Ольга Минакова", entity_key: "ольга минакова", entity_name: "Минакова Ольга", category: "судья", reason: "судья", active: true }],
  suggestions: [{ key: "иван петров", name: "Петров Иван", category: "полицейский", reason: "так в тексте" }],
  categories: [
    { value: "judge", label: "судья" },
    { value: "police", label: "полицейский" }
  ]
};

beforeEach(() => {
  for (const mock of [get, add, suggested, deactivate]) {
    mock.mockReset();
  }
  get.mockReturnValue(ok(LIST) as never);
});

it("adds a person with a category and a reason, and says a repeat", async () => {
  add.mockReturnValue(ok({ status: "already" }) as never);
  renderPage(<OfficialsPage />);

  fireEvent.change(await screen.findByLabelText("ФИО"), { target: { value: "Ольга Минакова" } });
  fireEvent.change(screen.getByLabelText("Категория"), { target: { value: "police" } });
  fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "следователь" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить" }));

  await waitFor(() => expect(add).toHaveBeenCalledWith({ body: { full_name: "Ольга Минакова", category: "police", reason: "следователь" } }));
  expect(await screen.findByText("Этот человек уже в списке.")).toBeTruthy();
});

it("accepts a suggestion and takes a person out of force", async () => {
  suggested.mockReturnValue(ok({ status: "added" }) as never);
  deactivate.mockReturnValue(ok({ status: "deactivated" }) as never);
  renderPage(<OfficialsPage />);

  const offered = await screen.findByRole("region", { name: "Предложения системы" });
  fireEvent.click(within(offered).getByRole("button", { name: "Добавить в список" }));
  await waitFor(() => expect(suggested).toHaveBeenCalledWith({ body: { key: "иван петров" } }));

  fireEvent.click(screen.getByRole("button", { name: "Снять" }));
  await waitFor(() => expect(deactivate).toHaveBeenCalledWith({ body: { external_id: "console:ольга минакова" } }));
  expect(screen.getByRole("link", { name: "Минакова Ольга" }).getAttribute("href")).toBe("/investigations/%D0%BE%D0%BB%D1%8C%D0%B3%D0%B0%20%D0%BC%D0%B8%D0%BD%D0%B0%D0%BA%D0%BE%D0%B2%D0%B0");
});
