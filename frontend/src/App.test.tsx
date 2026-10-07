import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";

import { App } from "@/App";

it("renders the migration shell", () => {
  render(<App />, { wrapper: MemoryRouter });
  expect(screen.getByRole("heading", { name: "court-monitor" })).toBeTruthy();
});
