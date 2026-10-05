// Every table sorts by a press on a column's header. The rows sorted are the ones on the
// page: a table that shows a part of a list sorts that part, and its header says so.
// Left alone: a table whose headers are links already (it is sorted by the server), one
// with merged cells, and a column with no name — the buttons of a row.
(function () {
  "use strict";
  const core = window.TableSortCore;
  if (!core) {
    return;
  }

  function cellText(cell) {
    // What is read, not what is pressed: the copy button and the forms say nothing.
    const copy = cell.cloneNode(true);
    copy.querySelectorAll("button, form, script, select, input").forEach(function (node) {
      node.remove();
    });
    return copy.textContent;
  }

  function sortBy(table, column, direction) {
    const body = table.tBodies[0];
    const width = table.tHead.rows[0].cells.length;
    const rows = Array.from(body.rows);
    // A row that is not a row of the table — «Записей нет», a line across it — stays
    // where the sorted rows end.
    const sortable = rows.filter(function (row) {
      return row.cells.length === width;
    });
    const others = rows.filter(function (row) {
      return row.cells.length !== width;
    });
    const texts = sortable.map(function (row) {
      return cellText(row.cells[column]);
    });
    core.order(texts, direction === "descending").forEach(function (index) {
      body.appendChild(sortable[index]);
    });
    others.forEach(function (row) {
      body.appendChild(row);
    });
    Array.from(table.tHead.rows[0].cells).forEach(function (header, index) {
      if (index === column) {
        header.setAttribute("aria-sort", direction);
      } else {
        header.removeAttribute("aria-sort");
      }
    });
  }

  document.querySelectorAll("table").forEach(function (table) {
    const head = table.tHead && table.tHead.rows.length === 1 ? table.tHead.rows[0] : null;
    const body = table.tBodies.length === 1 ? table.tBodies[0] : null;
    if (!head || !body || body.rows.length < 2) {
      return;
    }
    if (table.querySelector("thead a, [rowspan], thead [colspan]")) {
      return;
    }
    Array.from(head.cells).forEach(function (header, column) {
      const label = header.textContent.trim();
      if (!label) {
        return;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sort";
      button.title = "Сортировать строки на этой странице";
      while (header.firstChild) {
        button.appendChild(header.firstChild);
      }
      header.appendChild(button);
      button.addEventListener("click", function () {
        sortBy(table, column, core.nextDirection(header.getAttribute("aria-sort")));
      });
    });
  });
})();
