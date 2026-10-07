import { useEffect, useRef } from "react";

/** The legacy graph's scripts, from the same server: the library, the logic (with its
 * own node tests, tests/js) and the glue that draws into the markup below. */
const SCRIPTS = [
  "/static/vendor/vis-network/vis-network.min.js",
  "/static/investigation-graph-core.js",
  "/static/investigation-graph.js"
];

type GraphHandle = { destroy: () => void };

declare global {
  interface Window {
    InvestigationGraph?: { mount: (box: HTMLElement) => GraphHandle };
  }
}

// The switches the logic knows by these names (`investigation-graph-core.js`).
const FILTERS: [string, string, boolean][] = [
  ["events", "События", true],
  ["people", "Людей", true],
  ["publications", "Публикации", true],
  ["orgs", "Суды и органы", true],
  ["articles", "Статьи", true],
  ["cooccurrence", "Совместные упоминания", false]
];

function loadOnce(src: string): Promise<void> {
  const existing = document.querySelector<HTMLScriptElement>(`script[data-graph-src="${src}"]`);
  if (existing) {
    return existing.dataset.loaded ? Promise.resolve() : new Promise((done) => existing.addEventListener("load", () => done()));
  }
  return new Promise((done, fail) => {
    const script = document.createElement("script");
    script.src = src;
    script.dataset.graphSrc = src;
    script.addEventListener("load", () => {
      script.dataset.loaded = "1";
      done();
    });
    script.addEventListener("error", () => fail(new Error(src)));
    document.body.append(script);
  });
}

/** The interactive graph of a dossier, drawn by the legacy glue into this box. One graph
 * per person: the caller keys the component by the person, so another person is a new
 * box and a new graph, and the old one is destroyed — a late answer for it is dropped. */
export function EventGraph({ graphUrl, name }: { graphUrl: string; name: string }) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = box.current;
    if (!element) {
      return;
    }
    let cancelled = false;
    let graph: GraphHandle | null = null;
    SCRIPTS.reduce((chain, src) => chain.then(() => loadOnce(src)), Promise.resolve())
      .then(() => {
        // Left before the scripts came (or strict mode's first mount): draw nothing.
        if (!cancelled && window.InvestigationGraph) {
          graph = window.InvestigationGraph.mount(element);
        }
      })
      .catch(() => {
        const status = element.querySelector(".ig-status");
        if (!cancelled && status) {
          status.textContent = "Библиотека графа не загрузилась. Остальное досье от неё не зависит.";
        }
      });
    return () => {
      cancelled = true;
      graph?.destroy();
    };
  }, []);

  return (
    <div
      id="investigation-graph"
      ref={box}
      data-mount="manual"
      data-graph-url={graphUrl}
      data-expand-url={`${graphUrl}/expand`}
      className="space-y-2"
    >
      <fieldset className="flex flex-wrap gap-3 text-sm">
        <legend className="mb-1 font-medium">Показывать</legend>
        {FILTERS.map(([filter, label, on]) => (
          <label key={filter} className="flex items-center gap-1">
            <input type="checkbox" data-filter={filter} defaultChecked={on} /> {label}
          </label>
        ))}
      </fieldset>
      <div className="grid gap-3 lg:grid-cols-[1fr_18rem]">
        <div className="ig-canvas h-[520px] rounded-md border" role="application" aria-label={`Граф событий: ${name}`} />
        <div className="ig-panel rounded-md border p-3 text-sm" role="region" aria-live="polite" aria-label="Выбранный узел" />
      </div>
      <p className="flex flex-wrap items-center gap-3 text-sm">
        <button type="button" className="ig-reset rounded-md border px-3 py-1">
          Вернуть расположение
        </button>
        <span className="ig-status text-muted-foreground" role="status">
          Граф строится в браузере.
        </span>
      </p>
      <p className="text-xs text-muted-foreground">
        ● человек · ◆ событие · ■ публикация · ▲ суд · ▼ орган · ⬢ статья · сплошная линия — из текста публикации · пунктир —
        совместное упоминание · пунктирная рамка — узел ещё не раскрыт (двойное нажатие или кнопка в панели)
      </p>
    </div>
  );
}
