// Sorting the rows of a table by a column: what does not touch the page.
//
// A cell is text. What it means — a number, a day, a word — is read from the text, so a
// table needs no mark of its own to be sorted. Runs under node in the tests as it runs in
// the browser; the headers and the rows are in `table-sort.js`.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.TableSortCore = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // «05.10.2026» and «05.10.2026 11:33», as every page writes a day.
  const DAY = /^(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?$/;
  // «19 913», «0.0105», «3,5», «-2», with «#», «$», «%» or «№» beside it.
  const NUMBER = /^[#№$]?\s*(-?\d[\d\s ]*(?:[.,]\d+)?)\s*[%$]?$/;
  const EMPTY = /^[—–-]?$/;

  // What a cell is worth in an order: its kind, and the value to compare within the kind.
  function sortKey(text) {
    const value = String(text === null || text === undefined ? "" : text)
      .replace(/\s+/g, " ")
      .trim();
    if (EMPTY.test(value)) {
      return { kind: "empty", value: "" };
    }
    const day = DAY.exec(value);
    if (day) {
      const stamp = Date.UTC(+day[3], +day[2] - 1, +day[1], +(day[4] || 0), +(day[5] || 0));
      return { kind: "number", value: stamp };
    }
    const number = NUMBER.exec(value);
    if (number) {
      return { kind: "number", value: parseFloat(number[1].replace(/[\s ]/g, "").replace(",", ".")) };
    }
    return { kind: "text", value: value };
  }

  const collator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

  // Numbers before words, so a column of mostly numbers does not scatter them among text.
  function compareKeys(a, b) {
    if (a.kind !== b.kind) {
      return a.kind === "number" ? -1 : 1;
    }
    return a.kind === "number" ? a.value - b.value : collator.compare(a.value, b.value);
  }

  // The order of the rows whose cells of one column read `texts`: indices, sorted.
  // An empty cell is last either way — it is nothing to rank, not the least of all — and
  // rows that compare equal keep the order they had.
  function order(texts, descending) {
    const keyed = texts.map(function (text, index) {
      return { index: index, key: sortKey(text) };
    });
    keyed.sort(function (a, b) {
      const emptyA = a.key.kind === "empty";
      const emptyB = b.key.kind === "empty";
      if (emptyA || emptyB) {
        return emptyA === emptyB ? a.index - b.index : emptyA ? 1 : -1;
      }
      const compared = compareKeys(a.key, b.key);
      return (descending ? -compared : compared) || a.index - b.index;
    });
    return keyed.map(function (item) {
      return item.index;
    });
  }

  // The direction a press on a header gives: the other one if the column is sorted already.
  function nextDirection(current) {
    return current === "ascending" ? "descending" : "ascending";
  }

  return { nextDirection: nextDirection, order: order, sortKey: sortKey };
});
