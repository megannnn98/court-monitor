// The order a column gives, run by `node --test`
// (`tests/app/test_investigation_graph_js.py` runs every file here with the Python suite).
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../../src/static/table-sort-core.js");

const sorted = (texts, descending) => core.order(texts, descending).map((index) => texts[index]);

test("numbers sort as numbers, whatever stands beside them", () => {
  assert.deepEqual(sorted(["10", "9", "1 565", "19 913", "#72", "0,5", "$3.96", "40%"]), [
    "0,5",
    "$3.96",
    "9",
    "10",
    "40%",
    "#72",
    "1 565",
    "19 913",
  ]);
});

test("days sort as days and not as text, with or without the hour", () => {
  assert.deepEqual(sorted(["03.10.2026", "24.09.2026", "01.10.2025", "03.10.2026 11:33", "03.10.2026 9:05"]), [
    "01.10.2025",
    "24.09.2026",
    "03.10.2026",
    "03.10.2026 9:05",
    "03.10.2026 11:33",
  ]);
});

test("words sort the Russian way, ё with е and the case aside", () => {
  assert.deepEqual(sorted(["Яковлев", "ёлкин", "Егоров", "абрамов", "Ёжиков"]), [
    "абрамов",
    "Егоров",
    "Ёжиков",
    "ёлкин",
    "Яковлев",
  ]);
});

test("an empty cell is last in both directions", () => {
  const texts = ["5", "—", "", "12", "  "];
  assert.deepEqual(sorted(texts, false), ["5", "12", "—", "", "  "]);
  assert.deepEqual(sorted(texts, true), ["12", "5", "—", "", "  "]);
});

test("the other direction reverses the order; equal rows keep theirs", () => {
  const texts = ["б", "а", "б", "в"];
  assert.deepEqual(core.order(texts, false), [1, 0, 2, 3]);
  assert.deepEqual(core.order(texts, true), [3, 0, 2, 1]);
});

test("a column of numbers and words keeps the numbers together", () => {
  assert.deepEqual(sorted(["нет", "7", "да", "2"]), ["2", "7", "да", "нет"]);
  assert.deepEqual(sorted(["нет", "7", "да", "2"], true), ["нет", "да", "7", "2"]);
});

test("a press sorts up first, and down when the column is sorted up already", () => {
  assert.equal(core.nextDirection(null), "ascending");
  assert.equal(core.nextDirection("ascending"), "descending");
  assert.equal(core.nextDirection("descending"), "ascending");
});

test("what a cell is: nothing, a number, a day, a word", () => {
  assert.equal(core.sortKey(" — ").kind, "empty");
  assert.equal(core.sortKey("1 085").value, 1085);
  assert.equal(core.sortKey("14.02.1986").value, Date.UTC(1986, 1, 14));
  assert.equal(core.sortKey("ст. 205.2").kind, "text");
  assert.equal(core.sortKey("2026-10-03").kind, "text");
});
