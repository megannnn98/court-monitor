import { readFile } from "node:fs/promises";

import { beforeAll, beforeEach, expect, it } from "vitest";

/** The legacy pages' sorter (`src/static/table-sort*.js`), which the console calls for a
 * piece of a legacy page: once per header, and only under the root it is given. */
const TABLE = `<table><thead><tr><th>Имя</th><th>Число</th></tr></thead>
  <tbody><tr><td>б</td><td>2</td></tr><tr><td>а</td><td>10</td></tr><tr><td>в</td><td>1</td></tr></tbody></table>`;

let run;

beforeAll(async () => {
  const core = await readFile("../src/static/table-sort-core.js", "utf8");
  const page = await readFile("../src/static/table-sort.js", "utf8");
  // As two script tags run them: the core gives `window.TableSortCore`, the page uses it.
  run = () => {
    new Function("module", core)(undefined);
    new Function(page)();
  };
});

beforeEach(() => {
  document.body.innerHTML = `<div id="piece">${TABLE}</div><div id="other">${TABLE}</div>`;
  delete window.TableSort;
});

function names(root) {
  return Array.from(document.querySelectorAll(`#${root} tbody tr td:first-child`)).map((cell) => cell.textContent);
}

it("gives a header its button once, however often it is called", () => {
  run();
  window.TableSort.init(document.getElementById("piece"));
  window.TableSort.init(document);

  for (const header of document.querySelectorAll("th")) {
    expect(header.querySelectorAll("button.sort")).toHaveLength(1);
  }
  document.querySelector("#piece th button").click();
  // One press, one sorting: ascending, not sorted twice into descending.
  expect(names("piece")).toEqual(["а", "б", "в"]);
  expect(document.querySelector("#piece th").getAttribute("aria-sort")).toBe("ascending");
});

it("prepares only the tables under the root it is given", () => {
  run();
  document.body.innerHTML = `<div id="piece">${TABLE}</div><div id="other">${TABLE}</div>`;

  window.TableSort.init(document.getElementById("piece"));

  expect(document.querySelectorAll("#piece button.sort")).toHaveLength(2);
  expect(document.querySelectorAll("#other button.sort")).toHaveLength(0);
});
