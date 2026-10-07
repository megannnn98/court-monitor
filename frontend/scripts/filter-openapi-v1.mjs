import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

export function filterOpenApiV1(schema) {
  return {
    ...schema,
    paths: Object.fromEntries(
      Object.entries(schema.paths).filter(
        ([path, operations]) =>
          path.startsWith("/api/v1/") && Object.keys(operations).every((method) => method === "get"),
      ),
    ),
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
