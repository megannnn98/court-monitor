import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { decidePairV1, listPairsV1 } from "@/api/generated";
import { PairsPage } from "@/pages/PairsPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listPairsV1: vi.fn(), decidePairV1: vi.fn() }));
const list = vi.mocked(listPairsV1);
const decide = vi.mocked(decidePairV1);

function side(key: string, name: string) {
  return { key, name, role_label: null, rf_label: null, variants: [{ form: name, count: 2 }], mention_count: 2, article_count: 2, regions: [], articles: [] };
}
const PAGE = {
  kinds: [
    { value: "all", label: "Все", count: 1 },
    { value: "patronymic", label: "С отчеством и без", count: 1 }
  ],
  open_pairs: 1,
  total: 1,
  page: 1,
  page_size: 50,
  decided: {},
  items: [
    {
      kind: "patronymic",
      hint: "одно имя и фамилия, у одной сущности есть отчество, у другой нет",
      note: "Не слито автоматически: подходит сразу к нескольким людям.",
      left: side("игорь ранав", "Ранав Игорь"),
      right: side("игорь александрович ранав", "Ранав Игорь Александрович")
    }
  ]
};

beforeEach(() => {
  list.mockReset();
  decide.mockReset();
});

it("shows a pair side by side and decides it", async () => {
  list.mockReturnValue(ok(PAGE) as never);
  decide.mockReturnValue(ok({ key: "x", decision: "same" }) as never);

  renderPage(<PairsPage />);

  expect(await screen.findByRole("link", { name: "Ранав Игорь Александрович" })).toBeTruthy();
  expect(screen.getByText("Не слито автоматически: подходит сразу к нескольким людям.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Один человек" }));
  await waitFor(() =>
    expect(decide).toHaveBeenCalledWith({ body: { key_a: "игорь ранав", key_b: "игорь александрович ранав", decision: "same" } })
  );
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  expect(list).toHaveBeenCalledWith({ query: { kind: "all", page: 1, key: "" } });
});

it("says when no pair is open and shows the API's error", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0 }) as never);
  const { unmount } = renderPage(<PairsPage />, { url: "/?kind=similar" });
  expect(await screen.findByText("Спорных пар нет.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: { kind: "similar", page: 1, key: "" } });
  unmount();

  list.mockReturnValue(failed(500, "pairs unreadable") as never);
  renderPage(<PairsPage />);
  expect(await screen.findByText("pairs unreadable")).toBeTruthy();
});
