import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { BASE, HEADER, scopeLegacyCss } from "./scope-legacy-css.mjs";

describe("scopeLegacyCss", () => {
  it("puts every rule under the scope, each selector of a list", () => {
    expect(scopeLegacyCss("h1,\nh2 { margin: 0; }")).toBe(".legacy-html h1,\n.legacy-html h2 {\n  margin: 0;\n}");
  });

  it("keeps the legacy variables inside the scope and drops the page's own rules", () => {
    const scoped = scopeLegacyCss(":root { --muted: #64748b; }\nbody { margin: 0; }\nhtml, .band { color: red; }");

    expect(scoped).toBe(".legacy-html {\n  --muted: #64748b;\n}\n\n.legacy-html .band {\n  color: red;\n}");
  });

  it("drops the dark theme and keeps the narrow-screen rules", () => {
    const scoped = scopeLegacyCss(
      "@media (prefers-color-scheme: dark) { :root { --text: #fff; } }\n@media (max-width: 600px) { .band { padding: 8px; } }"
    );

    expect(scoped).toBe("@media (max-width: 600px) {\n  .legacy-html .band {\n    padding: 8px;\n  }\n}");
  });

  it("leaves what is no selector as it is", () => {
    expect(scopeLegacyCss("@keyframes pulse { from { opacity: 1; } to { opacity: 0; } }")).toContain("@keyframes pulse {");
    expect(scopeLegacyCss("/* a note */ .a { b: c; }")).toBe(".legacy-html .a {\n  b: c;\n}");
  });

  it("the committed stylesheet is what the legacy one gives today", async () => {
    const legacy = await readFile("../src/static/local-ui.css", "utf8");
    const committed = await readFile("src/legacy-html.css", "utf8");

    // Stale: run `npm run generate:legacy-css`.
    expect(committed).toBe(`${HEADER}${BASE}${scopeLegacyCss(legacy)}\n`);
  });
});
