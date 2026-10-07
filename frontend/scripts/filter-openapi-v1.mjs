import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

/** The versioned API only. Its unsafe methods are the console's actions, refused
 * from other origins (ADR 0022); which ones exist is fixed by the backend's route list
 * (tests/app/test_api_architecture.py). */
export function filterOpenApiV1(schema) {
  return {
    ...schema,
    paths: Object.fromEntries(Object.entries(schema.paths).filter(([path]) => path.startsWith("/api/v1/"))),
  };
}

async function main() {
  const source = "openapi/openapi.json";
  const schema = JSON.parse(await readFile(source, "utf8"));
  const filtered = filterOpenApiV1(schema);

  await writeFile(source, `${JSON.stringify(filtered, null, 2)}\n`);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
