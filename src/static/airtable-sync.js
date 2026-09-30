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
  const importButton = document.getElementById("import-button");
  const importFile = document.getElementById("official-file");
  const importStatus = document.getElementById("import-status");

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

  // ---- the published list ---------------------------------------------------
  if (importButton && importFile) {
    const url = importFile.dataset.url;

    importButton.addEventListener("click", async () => {
      const file = importFile.files[0];
      if (!file) {
        note(importStatus, "Сначала выберите файл.", "warning");
        return;
      }
      importButton.disabled = true;
      importStatus.textContent = "Загружаю…";
      try {
        const response = await fetch(url, {
          method: "POST",
          headers: {"Content-Type": "application/octet-stream"},
          body: file,
        });
        const report = await response.json();
        if (!response.ok) {
          note(importStatus, report.detail || "Ошибка " + response.status, "warning");
          return;
        }
        if (report.status === "unchanged") {
          note(importStatus, "Перечень не изменился — снимок прежний.", "muted");
        } else if (report.status === "imported") {
          // A new snapshot has no match rows yet, so «Кандидаты» would find nothing
          // until the RF stage runs. Said here, where the button was pressed, rather
          // than left for the operator to discover on an empty page.
          note(
            importStatus,
            "Готово: снимок #" +
              report.snapshot_id +
              " от " +
              new Date(report.snapshot_date).toLocaleString("ru-RU") +
              ", записей " +
              report.entries.toLocaleString("ru-RU") +
              ". Теперь сверьте людей с новым перечнем — запустите шаг «сверить с РФМ» " +
              "на странице «Журнал запусков», иначе «Кандидаты» покажут пустоту.",
            "muted"
          );
        } else {
          note(importStatus, report.detail || "Файл не распознан как перечень.", "warning");
        }
      } catch (error) {
        note(importStatus, String(error), "warning");
      } finally {
        importButton.disabled = false;
      }
    });
  }
})();
