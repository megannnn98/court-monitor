import { readFile, writeFile } from "node:fs/promises";

const source = "openapi/openapi.json";
const schema = JSON.parse(await readFile(source, "utf8"));

schema.paths = Object.fromEntries(
  Object.entries(schema.paths).filter(([path, operations]) =>
    path.startsWith("/api/v1/") && Object.keys(operations).every((method) => method === "get")
  )
);

await writeFile(source, `${JSON.stringify(schema, null, 2)}\n`);
