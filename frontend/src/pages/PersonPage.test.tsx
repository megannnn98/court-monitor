import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getPersonDetailV1 } from "@/api/generated";
import { PersonPage } from "@/pages/PersonPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getPersonDetailV1: vi.fn() }));
const detail = vi.mocked(getPersonDetailV1);

const PERSON = { id: 9, canonical_name: "Олег Орлов", normalized_name: "олег орлов", matching_key: "k", status: "active" };

beforeEach(() => detail.mockReset());

it("shows the facts, the aliases and the events with their span", async () => {
  detail.mockReturnValue(
    ok({
      person: PERSON,
      aliases: [{ id: 1, person_id: 9, surface_text: "Орлова Олега", normalized_text: "орлов олег", matching_key: "k", origin: "extraction", confidence: 0.8 }],
      persecution: {
        id: 3,
        person_id: 9,
        status: "political",
        confidence: 0.95,
        reasons: ["discreditation"],
        evidence_types: ["article"],
        classifier_name: "rules",
        classifier_version: "v2"
      },
      rosfinmonitoring: { snapshot_id: 12, status: "not_matched", confidence: 0, matched_entry_id: null, matched_entry_name: null, reasons: [] },
      events: [
        {
          id: 40,
          event_type: "sentence",
          event_date: "2024-02-27",
          role: "accused",
          confidence: 0.9,
          evidence: { article_id: 77, source_name: "ovd", title: "Приговор Орлову", url: "https://x", start_offset: 10, end_offset: 30, text: "суд приговорил Олега Орлова" }
        }
      ]
    }) as never
  );

  renderPage(<PersonPage />, { path: "/persons/:personId", url: "/persons/9" });

  expect(await screen.findByRole("heading", { name: "Олег Орлов" })).toBeTruthy();
  expect(detail).toHaveBeenCalledWith({ path: { person_id: 9 } });
  expect(screen.getByText("political")).toBeTruthy();
  expect(screen.getByText("not_matched")).toBeTruthy();
  expect(screen.getByText("Орлова Олега")).toBeTruthy();
  expect(screen.getByText("27.02.2024")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Приговор Орлову" }).getAttribute("href")).toBe("/articles/77?start=10&end=30");
});

it("says when a person has no aliases and no events", async () => {
  detail.mockReturnValue(ok({ person: PERSON, aliases: [], persecution: null, rosfinmonitoring: null, events: [] }) as never);

  renderPage(<PersonPage />, { path: "/persons/:personId", url: "/persons/9" });

  expect(await screen.findByText("Событий нет.")).toBeTruthy();
  expect(screen.getByText("Алиасов нет.")).toBeTruthy();
});

it("shows the API's own error", async () => {
  detail.mockReturnValue(failed(404, "Person not found") as never);

  renderPage(<PersonPage />, { path: "/persons/:personId", url: "/persons/404" });

  expect(await screen.findByText("Person not found")).toBeTruthy();
});
