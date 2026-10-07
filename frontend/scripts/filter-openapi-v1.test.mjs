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

  it("removes a versioned endpoint with POST", () => {
    const filtered = filterOpenApiV1(
      schema({ "/api/v1/x": { get: { operationId: "x" }, post: { operationId: "createX" } } }),
    );

    expect(filtered.paths).toEqual({});
  });

  it("removes a versioned endpoint with GET and DELETE", () => {
    const filtered = filterOpenApiV1(
      schema({ "/api/v1/x": { get: { operationId: "x" }, delete: { operationId: "deleteX" } } }),
    );

    expect(filtered.paths).toEqual({});
  });
});
