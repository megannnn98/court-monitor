import { expect, it } from "vitest";

import { errorMessage } from "@/lib/api";

it("reads FastAPI's detail, a string or a list of problems", () => {
  expect(errorMessage({ detail: "Person not found" }, 404)).toBe("Person not found");
  expect(errorMessage({ detail: [{ msg: "field required" }, { msg: "too long" }] }, 422)).toBe("field required; too long");
});

it("reads the app's own error body", () => {
  expect(
    errorMessage({ error: { code: "csrf_refused", message: "cross-origin request refused: https://evil.example", request_id: "x" } }, 403)
  ).toBe("cross-origin request refused: https://evil.example");
});

it("falls back to the status, or to the silence", () => {
  expect(errorMessage({ detail: null }, 500)).toBe("Сервер ответил 500");
  expect(errorMessage(undefined, 502)).toBe("Сервер ответил 502");
  expect(errorMessage(undefined, undefined)).toBe("Сервер не ответил");
});
