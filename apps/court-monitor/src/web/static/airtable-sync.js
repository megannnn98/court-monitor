// The «Справочники» page: sync the four lists, and bring in a published Rosfinmonitoring
// list from a file when the site cannot be reached.
//
// Both answer in place: the page is never reloaded. The file is posted as its raw
// bytes, not as multipart — the bytes are all the server needs, and that keeps
// python-multipart (a dependency-layer rebuild) out of the project.
(function () {
  const syncButton = document.getElementById("sync-button");
  const syncStatus = document.getElementById("sync-status");
  const syncResult = document.getElementById("sync-result");
  const syncConfig = document.getElementById("airtable-sync-config");

  function cell(row, text, cls) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    row.appendChild(td);
  }

  function note(target, text, kind) {
    target.replaceChildren();
    const p = document.createElement("p");
    p.className = kind || "muted";
    p.textContent = text;
    target.appendChild(p);
  }

  // ---- the four lists -------------------------------------------------------
  if (syncButton && syncConfig) {
    const {url, labels, order} = JSON.parse(syncConfig.textContent);

    function stamp(value) {
      return new Date(value).toLocaleString("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }

    function paint(report) {
      syncStatus.textContent =
        "Последняя синхронизация: " +
        stamp(report.started_at) +
        (report.mode === "files" ? " (из файлов)" : "");

      const table = document.createElement("table");
      const head = table.createTHead().insertRow();
      for (const title of ["Справочник", "Создано", "Обновлено", "Без изменений", "Статус"]) {
        cell(head, title);
      }
      const body = table.createTBody();
      for (const name of order) {
        const item = report.tables[name] || {};
        const failed = item.status === "error" || Boolean(item.error);
        const skipped = item.status === "skipped";
        const row = body.insertRow();
        cell(row, labels[name] || name);
        cell(row, String(item.created ?? 0), "num");
        cell(row, String(item.updated ?? 0), "num");
        cell(row, String(item.unchanged ?? 0), "num");
        cell(row, failed ? "ошибка" : skipped ? "нет файла" : "успешно", failed ? "error-text" : "");
        if (item.removed || item.removed_blocked) {
          // The list is a copy of an Airtable view, so rows that left the view leave the
          // copy. Said here, because a list that quietly shrank is one nobody trusts —
          // and a refused removal matters even more than a performed one.
          const note = row.insertCell();
          note.className = item.removed_blocked ? "error-text" : "muted";
          note.colSpan = 2;
          note.textContent = item.removed_blocked
            || `удалено строк, которых больше нет в представлении: ${item.removed}`;
        }
        if (failed || skipped) {
          // Which list went wrong, said plainly: one error must not read as "nothing
          // happened", and a list nobody exported must not look like an empty one.
          const detail = row.insertCell();
          detail.className = "error-text";
          detail.colSpan = 2;
          detail.textContent = item.error || "";
        }
      }

      const verdict = {success: "успешно", partial: "завершено частично", failed: "ошибка"}[
        report.status
      ];
      const summary = document.createElement("p");
      summary.className = report.status === "success" ? "muted" : "warning";
      summary.textContent = "Статус: " + (verdict || report.status);

      syncResult.replaceChildren(table, summary);
    }

    syncButton.addEventListener("click", async () => {
      syncButton.disabled = true;
      syncStatus.textContent = "Синхронизация...";
      syncResult.replaceChildren();
      try {
        const response = await fetch(url, {method: "POST"});
        const report = await response.json();
        if (!response.ok) {
          note(syncResult, report.detail || "Ошибка " + response.status, "warning");
          syncStatus.textContent = "Синхронизация не выполнена";
          return;
        }
        paint(report);
      } catch (error) {
        note(syncResult, String(error), "warning");
        syncStatus.textContent = "Синхронизация не выполнена";
      } finally {
        syncButton.disabled = false;
      }
    });
  }

})();
