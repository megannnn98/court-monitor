// The graph's logic that does not touch the page, run by `node --test`
// (`tests/app/test_investigation_graph_js.py` runs it with the Python suite).
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../../src/static/investigation-graph-core.js");

const FIRST = {
  center: "person:1",
  nodes: [
    { id: "person:1", type: "person", label: "Иванов Иван", href: "/ui/investigations/x", expandable: true },
    {
      id: "event:7",
      type: "event",
      label: "Задержание",
      date: "2026-06-14",
      dated: false,
      confidence: 0.956,
      publication_source: "ОВД-Инфо",
      publication_title: "<b>Задержание</b>",
      href: "/ui/articles/9?start=3&end=40",
      expandable: true,
    },
    { id: "person:3", type: "person", label: "Смирнова Анна", href: "/ui/investigations/s", expandable: true },
  ],
  edges: [
    { id: "person:1>event:7:target", from: "person:1", to: "event:7", type: "target", label: "назван в событии", source: "extracted" },
    { id: "person:1>person:3:cooccurrence", from: "person:1", to: "person:3", type: "cooccurrence", label: "общих публикаций: 4", source: "derived", shared_publications: 4 },
  ],
  more: [{ node: "person:1", type: "event", count: 37 }],
};
const OPENED = {
  nodes: [
    FIRST.nodes[1],
    { id: "person:2", type: "person", label: "Петров Пётр", href: "/ui/investigations/p", expandable: true },
    { id: "publication:9", type: "publication", label: "Задержание двоих", publication_source: "ОВД-Инфо", date: "2026-06-14", href: "/ui/articles/9", expandable: false },
    { id: "org:court:тверской суд", type: "court", label: "Тверской суд", href: "/ui/publications?q=x", expandable: false },
    { id: "article:УК РФ:280.3", type: "criminal_article", label: "ст. 280.3 УК РФ", href: "/ui/entities?article=280.3", expandable: false },
  ],
  edges: [
    FIRST.edges[0],
    { id: "person:2>event:7:target", from: "person:2", to: "event:7", type: "target", label: "назван в событии", source: "extracted" },
    { id: "event:7>publication:9:evidence", from: "event:7", to: "publication:9", type: "evidence", label: "источник", source: "extracted", href: "/ui/articles/9?start=3&end=40" },
    { id: "event:7>org:court:тверской суд:court", from: "event:7", to: "org:court:тверской суд", type: "court", label: "суд", source: "extracted" },
    { id: "event:7>article:УК РФ:280.3:legal_basis", from: "event:7", to: "article:УК РФ:280.3", type: "legal_basis", label: "статья", source: "extracted", title: "ч. 1" },
  ],
  more: [{ node: "event:7", type: "unresolved_person", count: 2 }],
};

function loaded() {
  const state = core.createState();
  core.merge(state, FIRST);
  core.merge(state, OPENED);
  return state;
}

function shown(state, filters) {
  const off = core.hidden(state, Object.assign({}, core.DEFAULT_FILTERS, filters));
  return Array.from(state.nodes.keys())
    .filter((id) => !off.nodes.has(id))
    .sort();
}

test("an answer adds only what is new, and a second opening adds nothing", () => {
  const state = core.createState();
  const first = core.merge(state, FIRST);
  assert.equal(state.center, "person:1");
  assert.deepEqual([first.nodes.length, first.edges.length], [3, 2]);

  const opened = core.merge(state, OPENED);
  assert.deepEqual(opened.nodes.map((node) => node.id), ["person:2", "publication:9", "org:court:тверской суд", "article:УК РФ:280.3"]);
  assert.equal(opened.edges.length, 4);

  const again = core.merge(state, OPENED);
  assert.deepEqual([again.nodes.length, again.edges.length], [0, 0]);
  assert.deepEqual([state.nodes.size, state.edges.size], [7, 6]);
  // The centre is the dossier's, whatever a later answer says.
  core.merge(state, { center: "person:2", nodes: [], edges: [] });
  assert.equal(state.center, "person:1");
});

test("an edge to a node that was not sent is not drawn", () => {
  const state = core.createState();
  const added = core.merge(state, {
    nodes: [FIRST.nodes[0]],
    edges: [FIRST.edges[0]],
  });
  assert.equal(added.edges.length, 0);
});

test("co-occurrence is hidden until asked for, with the people it alone holds", () => {
  const state = loaded();
  assert.deepEqual(shown(state, {}), ["article:УК РФ:280.3", "event:7", "org:court:тверской суд", "person:1", "person:2", "publication:9"]);
  assert.ok(core.hidden(state, core.DEFAULT_FILTERS).edges.has("person:1>person:3:cooccurrence"));
  assert.ok(shown(state, { cooccurrence: true }).includes("person:3"));
});

test("a filter hides its kind and what is left with nothing to hold it", () => {
  const state = loaded();
  assert.deepEqual(shown(state, { publications: false }).includes("publication:9"), false);
  assert.deepEqual(shown(state, { orgs: false, articles: false }), ["event:7", "person:1", "person:2", "publication:9"]);
  // Without the events nothing else has an edge to show; the person of the dossier stays.
  assert.deepEqual(shown(state, { events: false }), ["person:1"]);
  assert.deepEqual(shown(state, { people: false }), ["article:УК РФ:280.3", "event:7", "org:court:тверской суд", "person:1", "publication:9"]);
  const off = core.hidden(state, Object.assign({}, core.DEFAULT_FILTERS, { people: false }));
  assert.ok(off.edges.has("person:2>event:7:target") && !off.edges.has("person:1>event:7:target"));
});

test("only an address inside the console is a link", () => {
  assert.equal(core.safeHref("/ui/articles/9?start=3&end=40"), "/ui/articles/9?start=3&end=40");
  for (const bad of ["javascript:alert(1)", "//evil.example/ui/", "https://evil.example", "/ui/x //y", "/api/x", "", null, undefined, 7]) {
    assert.equal(core.safeHref(bad), null);
  }
});

test("the panel of an event says its date, who is named, its source and where to read it", () => {
  const told = core.details(loaded(), "event:7");
  assert.equal(told.title, "Задержание");
  assert.equal(told.kind, "Событие");
  assert.deepEqual(told.lines, [
    "14.06.2026 — дата публикации: своей даты у события нет",
    "Иванов Иван: назван в событии",
    "Петров Пётр: назван в событии",
    "Уверенность извлечения: 0.96",
    "Источник: ОВД-Инфо",
    "Публикация: <b>Задержание</b>",
    "Суд: Тверской суд",
    "Статья УК: ст. 280.3 УК РФ (ч. 1)",
    "+ 2 имени без карточки",
  ]);
  assert.deepEqual(told.links, [{ label: "Открыть место в публикации", href: "/ui/articles/9?start=3&end=40" }]);
  assert.equal(told.expand, "Показать связи");
});

test("the panel of a person counts shared events and names co-occurrence for what it is", () => {
  const state = loaded();
  const petrov = core.details(state, "person:2");
  assert.deepEqual(petrov.lines, ["Общих событий в графе: 1", "Событий в графе: 1"]);
  assert.deepEqual(petrov.links, [{ label: "Открыть досье", href: "/ui/investigations/p" }]);
  assert.equal(petrov.expand, "Показать события");

  const smirnova = core.details(state, "person:3");
  assert.deepEqual(smirnova.lines, [
    "Общих событий в графе: 0",
    "Общих публикаций: 4 — это только совместное упоминание, не установленная связь",
    "Событий в графе: 0",
  ]);

  const centre = core.details(state, "person:1");
  assert.deepEqual(centre.lines, ["Событий в графе: 1", "+ 37 событий"]);
  state.expanded.add("person:1");
  assert.equal(core.details(state, "person:1").expand, null);
});

test("the panel of a publication leads to the event's very words", () => {
  const told = core.details(loaded(), "publication:9");
  assert.deepEqual(told.lines, ["Источник: ОВД-Инфо", "14.06.2026"]);
  assert.deepEqual(told.links, [
    { label: "Открыть место события: Задержание", href: "/ui/articles/9?start=3&end=40" },
    { label: "Открыть публикацию", href: "/ui/articles/9" },
  ]);
  assert.equal(told.expand, null);
});

test("a court and an article say what they are and do not open", () => {
  const state = loaded();
  const court = core.details(state, "org:court:тверской суд");
  assert.equal(court.kind, "Суд");
  assert.equal(court.lines[0], "Событий в графе: 1");
  assert.deepEqual(court.links, [{ label: "Публикации с этим названием", href: "/ui/publications?q=x" }]);
  const article = core.details(state, "article:УК РФ:280.3");
  assert.deepEqual(article.links, [{ label: "Люди с этой статьёй", href: "/ui/entities?article=280.3" }]);
  assert.equal(article.expand, null);
  assert.equal(core.details(state, "nothing"), null);
});

test("a link made of scraped text is dropped", () => {
  const state = core.createState();
  core.merge(state, { center: "person:1", nodes: [Object.assign({}, FIRST.nodes[0], { href: "javascript:alert(1)" })], edges: [] });
  assert.deepEqual(core.details(state, "person:1").links, []);
});

test("what a limit cut is said in words", () => {
  assert.equal(core.moreText("event", 1), "+ 1 событие");
  assert.equal(core.moreText("event", 3), "+ 3 события");
  assert.equal(core.moreText("event", 37), "+ 37 событий");
  assert.equal(core.moreText("event", 11), "+ 11 событий");
  assert.equal(core.moreText("person", 22), "+ 22 человека");
  assert.equal(core.moreText("unresolved_person", 5), "+ 5 имён без карточки");
});

test("the shape says the kind, and a weak edge is dashed", () => {
  const state = loaded();
  const shape = (id) => core.visNode(state.nodes.get(id), state.center);
  assert.deepEqual(
    ["person:1", "event:7", "publication:9", "org:court:тверской суд", "article:УК РФ:280.3"].map((id) => shape(id).shape),
    ["dot", "diamond", "square", "triangle", "hexagon"]
  );
  assert.equal(shape("event:7").label, "Задержание\n14.06.2026");
  assert.ok(shape("person:1").size > shape("person:2").size);
  assert.deepEqual([shape("person:1").dashed, shape("person:2").dashed, shape("publication:9").dashed], [false, true, false]);
  assert.equal(core.visNode({ id: "x", type: "publication", label: "д".repeat(40) }, null).label.length, 30);

  const fact = core.visEdge(state.edges.get("person:1>event:7:target"));
  const count = core.visEdge(state.edges.get("person:1>person:3:cooccurrence"));
  // What an edge is stands in its tooltip; only the count of shared publications is
  // written on the line.
  assert.deepEqual([fact.dashes, fact.arrows, fact.label, fact.title], [false, "to", "", "назван в событии"]);
  assert.deepEqual([count.dashes, count.arrows, count.label], [true, "", "общих публикаций: 4"]);
  assert.equal(core.visEdge(state.edges.get("event:7>article:УК РФ:280.3:legal_basis")).title, "статья (ч. 1)");
  // A hypothesis an operator draws by hand must never look like a fact.
  assert.equal(core.visEdge({ id: "m", from: "a", to: "b", type: "linked", label: "", source: "manual" }).dashes, true);
});
