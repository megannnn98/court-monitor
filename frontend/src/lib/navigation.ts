/** The menu, in the legacy UI's groups and words (`src/web/ui/layout.py`, `_nav`).
 *
 * One entry per item. `path` is set once the page lives in React; until then the item
 * opens the legacy page at `legacy`. Moving a page is a change to its `path` here. */
export type NavItem = {
  key: string;
  label: string;
  legacy: string | null;
  path?: string;
  /** A counter from `/api/v1/status` beside the label, as in the legacy menu. */
  count?: "queue" | "result";
};

export type NavGroup = {
  title: string;
  items: NavItem[];
};

export const NAV: NavGroup[] = [
  {
    title: "Каждый день",
    items: [
      { key: "cycle", label: "Работа", legacy: "/ui/cycle", path: "/work", count: "queue" },
      // The review stations (pairs, roles, politics) are not in the menu: «Работа» opens them.
      { key: "political", label: "Результаты", legacy: "/ui/political", path: "/political", count: "result" },
      { key: "investigations", label: "Найти человека", legacy: "/ui/investigations", path: "/investigations" },
      { key: "ask", label: "Спросить", legacy: "/ui/ask" }
    ]
  },
  {
    title: "Данные",
    items: [
      { key: "entities", label: "Все люди", legacy: "/ui/entities", path: "/entities" },
      { key: "publications", label: "Публикации", legacy: "/ui/publications", path: "/publications" },
      { key: "sentences", label: "Приговоры", legacy: "/ui/sentences", path: "/sentences" },
      { key: "rfm", label: "Перечень РФМ", legacy: "/ui/rfm", path: "/rfm" },
      { key: "airtable", label: "База Airtable", legacy: "/ui/airtable" },
      // Not in the legacy menu: reachable there only by address.
      { key: "candidates", label: "Кандидаты", legacy: "/ui/candidates", path: "/candidates" },
      { key: "persons", label: "Персоны", legacy: null, path: "/persons" }
    ]
  },
  {
    title: "Система",
    items: [
      { key: "management", label: "Журнал запусков", legacy: "/ui/runs", path: "/runs" },
      { key: "monitoring", label: "Мониторинг", legacy: null, path: "/monitoring" },
      { key: "logs", label: "Логи", legacy: "/ui/logs", path: "/logs" },
      { key: "wiki", label: "Вики", legacy: "/ui/wiki" },
      { key: "about", label: "О системе", legacy: "/ui/about", path: "/about" }
    ]
  }
];

export function isMoved(item: NavItem): item is NavItem & { path: string } {
  return item.path !== undefined;
}

/** The dossier of a person in the new console, by the person's key. */
export function dossierPath(key: string): string {
  return `/investigations/${encodeURIComponent(key)}`;
}
