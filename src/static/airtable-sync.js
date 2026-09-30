// «Синхронизировать Airtable»: run one sync and paint the answer where it was pressed.
//
// The server refuses a second sync with 409, so the disabled button is a courtesy, not
// the guard. The page is never reloaded: the result is written into #sync-result.
(function () {
  const button = document.getElementById("sync-button");
  const status = document.getElementById("sync-status");
  const result = document.getElementById("sync-result");
  const config = document.getElementById("airtable-sync-config");
  if (!button || !config) return;

  const {url, labels, order} = JSON.parse(config.textContent);

  function cell(row, text, cls) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    row.appendChild(td);
  }

  function stamp(value) {
    return new Date(value).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function fail(message) {
    status.textContent = "Синхронизация не выполнена";
    const warning = document.createElement("p");
    warning.className = "warning";
    warning.textContent = message;
    result.replaceChildren(warning);
  }

  function paint(report) {
    status.textContent = "Последняя синхронизация: " + stamp(report.started_at);

    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    for (const title of ["Справочник", "Создано", "Обновлено", "Без изменений", "Статус"]) {
      cell(head, title);
    }
    const body = table.createTBody();
    for (const name of order) {
      const item = report.tables[name] || {};
      const failed = Boolean(item.error);
      const row = body.insertRow();
      cell(row, labels[name] || name);
      cell(row, String(item.created ?? 0), "num");
      cell(row, String(item.updated ?? 0), "num");
      cell(row, String(item.unchanged ?? 0), "num");
      cell(row, failed ? "ошибка" : "успешно", failed ? "error-text" : "");
      if (failed) {
        // Which table failed, said plainly: one error must not read as "nothing happened".
        const detail = row.insertCell();
        detail.className = "error-text";
        detail.colSpan = 2;
        detail.textContent = item.error;
      }
    }

    const verdict = {success: "успешно", partial: "завершено частично", failed: "ошибка"}[
      report.status
    ];
    const summary = document.createElement("p");
    summary.className = report.status === "success" ? "muted" : "warning";
    summary.textContent = "Статус: " + (verdict || report.status);

    result.replaceChildren(table, summary);
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Синхронизация...";
    result.replaceChildren();
    try {
      const response = await fetch(url, {method: "POST"});
      const report = await response.json();
      if (!response.ok) {
        fail(report.detail || "Ошибка " + response.status);
        return;
      }
      paint(report);
    } catch (error) {
      fail(String(error));
    } finally {
      button.disabled = false;
    }
  });
})();
