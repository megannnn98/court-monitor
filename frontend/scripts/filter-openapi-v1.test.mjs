import { describe, expect, it } from "vitest";

import { filterOpenApiV1 } from "./filter-openapi-v1.mjs";

function schema(paths) {
  return { openapi: "3.1.0", paths };
}

describe("filterOpenApiV1", () => {
  it("keeps a GET endpoint under /api/v1", () => {
    const filtered = filterOpenApiV1(schema({ "/api/v1/x": { get: { operationId: "x" } } }));

    expect(filtered.paths).toEqual({ "/api/v1/x": { get: { operationId: "x" } } });
  });

  it("removes endpoints outside /api/v1", () => {
    const filtered = filterOpenApiV1(schema({ "/persons": { get: { operationId: "persons" } } }));

    expect(filtered.paths).toEqual({});
  });

  it("keeps the actions of a versioned endpoint", () => {
    const paths = { "/api/v1/x": { get: { operationId: "x" }, post: { operationId: "createX" } } };

    expect(filterOpenApiV1(schema(paths)).paths).toEqual(paths);
  });

  it("keeps every versioned endpoint and drops the rest", () => {
    const filtered = filterOpenApiV1(
      schema({
        "/api/v1/x": { post: { operationId: "doX" } },
        "/ui/x": { post: { operationId: "legacyX" } },
        "/api/investigations/k/graph": { get: { operationId: "graph" } }
      })
    );

    expect(Object.keys(filtered.paths)).toEqual(["/api/v1/x"]);
  });
});
