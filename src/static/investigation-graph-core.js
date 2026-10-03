// The graph of an investigation: what does not touch the page.
//
// Everything here is a function of plain values — what the API answered, which filters
// are on — so it runs under node in the tests as it runs in the browser. The drawing, the
// requests and the panel are in `investigation-graph.js`.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.InvestigationGraphCore = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const COOCCURRENCE = "cooccurrence";

  // The switches of the page, and the node types each one shows. «Совместные упоминания»
  // is a kind of edge, not of node: two people in the same publications is a count, and
  // it is off until asked for.
  const GROUPS = {
    events: ["event"],
    people: ["person"],
    publications: ["publication"],
    orgs: ["court", "authority"],
    articles: ["criminal_article", "administrative_article"],
  };
  const DEFAULT_FILTERS = {
    events: true,
    people: true,
    publications: true,
    orgs: true,
    articles: true,
    cooccurrence: false,
  };
  const TYPE_NAMES = {
    person: "Человек",
    event: "Событие",
    publication: "Публикация",
    court: "Суд",
    authority: "Орган",
    criminal_article: "Статья УК",
    administrative_article: "Статья КоАП",
  };
  // The shape says the kind; the colour only repeats it.
  const SHAPES = {
    person: "dot",
    event: "diamond",
    publication: "square",
    court: "triangle",
    authority: "triangleDown",
    criminal_article: "hexagon",
    administrative_article: "hexagon",
  };
  const COLOURS = {
    person: "person",
    event: "event",
    publication: "publication",
    court: "org",
    authority: "org",
    criminal_article: "article",
    administrative_article: "article",
  };
  const MORE_WORDS = {
    event: ["событие", "события", "событий"],
    person: ["человек", "человека", "человек"],
    unresolved_person: ["имя без карточки", "имени без карточки", "имён без карточки"],
  };

  function createState() {
    return { center: null, nodes: new Map(), edges: new Map(), more: new Map(), expanded: new Set() };
  }

  // Adds what an answer brings and returns only what is new: opening a node twice, or
  // reaching the same event from two people, adds nothing the second time.
  function merge(state, payload) {
    const added = { nodes: [], edges: [] };
    if (payload.center && state.center === null) {
      state.center = payload.center;
    }
    (payload.nodes || []).forEach(function (node) {
      if (!state.nodes.has(node.id)) {
        state.nodes.set(node.id, node);
        added.nodes.push(node);
      }
    });
    (payload.edges || []).forEach(function (edge) {
      if (!state.edges.has(edge.id) && state.nodes.has(edge.from) && state.nodes.has(edge.to)) {
        state.edges.set(edge.id, edge);
        added.edges.push(edge);
      }
    });
    (payload.more || []).forEach(function (cut) {
      const of = state.more.get(cut.node) || {};
      of[cut.type] = cut.count;
      state.more.set(cut.node, of);
    });
    return added;
  }

  function groupOf(type) {
    return Object.keys(GROUPS).find(function (group) {
      return GROUPS[group].indexOf(type) !== -1;
    });
  }

  // What the filters hide among what is loaded. A node goes with its kind; an edge goes
  // with either of its ends, and a co-occurrence edge with its own switch; what is left
  // with no edge to show goes too. The person the dossier is of always stays.
  function hidden(state, filters) {
    const nodes = new Set();
    const edges = new Set();
    state.nodes.forEach(function (node, id) {
      const group = groupOf(node.type);
      if (id !== state.center && group !== undefined && !filters[group]) {
        nodes.add(id);
      }
    });
    const connected = new Set();
    state.edges.forEach(function (edge, id) {
      if (
        nodes.has(edge.from) ||
        nodes.has(edge.to) ||
        (edge.type === COOCCURRENCE && !filters.cooccurrence)
      ) {
        edges.add(id);
      } else {
        connected.add(edge.from);
        connected.add(edge.to);
      }
    });
    state.nodes.forEach(function (node, id) {
      if (id !== state.center && !connected.has(id)) {
        nodes.add(id);
      }
    });
    return { nodes: nodes, edges: edges };
  }

  // Only an address inside the console is a link: a label, a title and a name come from
  // scraped text, and an address made of them must not leave the site or run a script.
  function safeHref(href) {
    return typeof href === "string" && /^\/ui\/[^\s]*$/.test(href) && href.indexOf("//") === -1
      ? href
      : null;
  }

  function formatDate(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    return match ? match[3] + "." + match[2] + "." + match[1] : "";
  }

  function plural(count, words) {
    const tens = count % 100;
    const ones = count % 10;
    if (tens >= 11 && tens <= 14) {
      return words[2];
    }
    return ones === 1 ? words[0] : ones >= 2 && ones <= 4 ? words[1] : words[2];
  }

  // «+ 37 событий»: what a limit cut, in words.
  function moreText(type, count) {
    const words = MORE_WORDS[type];
    return "+ " + count + " " + (words ? plural(count, words) : type);
  }

  function short(label, size) {
    return label.length <= size ? label : label.slice(0, size - 1) + "…";
  }

  function visNode(node, center) {
    const date = node.type === "event" ? formatDate(node.date) : "";
    return {
      id: node.id,
      label: short(String(node.label), 30) + (date ? "\n" + date : ""),
      shape: SHAPES[node.type] || "dot",
      colour: COLOURS[node.type] || "publication",
      size: node.id === center ? 22 : node.type === "event" ? 16 : 12,
      borderWidth: node.id === center ? 3 : node.expandable ? 2 : 1,
      // Not opened yet: a dashed border invites the tap.
      dashed: Boolean(node.expandable) && node.id !== center,
    };
  }

  // An edge says what it is on hover and in the panel. Only the count of shared
  // publications is written on the line itself: twenty «назван в событии» around one
  // person say nothing and hide the dates.
  function visEdge(edge) {
    const weak = edge.type === COOCCURRENCE || edge.source !== "extracted";
    const label = String(edge.label || "");
    return {
      id: edge.id,
      from: edge.from,
      to: edge.to,
      label: edge.type === COOCCURRENCE ? label : "",
      title: label + (edge.title ? " (" + edge.title + ")" : ""),
      dashes: weak,
      arrows: edge.type === COOCCURRENCE ? "" : "to",
    };
  }

  function edgesOf(state, id) {
    const found = [];
    state.edges.forEach(function (edge) {
      if (edge.from === id || edge.to === id) {
        found.push(edge);
      }
    });
    return found;
  }

  function neighbours(state, id, type) {
    return edgesOf(state, id)
      .filter(function (edge) {
        return edge.type !== COOCCURRENCE;
      })
      .map(function (edge) {
        return state.nodes.get(edge.from === id ? edge.to : edge.from);
      })
      .filter(function (node) {
        return node && (type === undefined || node.type === type);
      });
  }

  // What the panel says of a node: a title, lines of plain text, links inside the console
  // and whether it opens. Plain values only — the page writes them with `textContent`.
  function details(state, id) {
    const node = state.nodes.get(id);
    if (!node) {
      return null;
    }
    const lines = [];
    const links = [];
    const link = function (label, href) {
      const safe = safeHref(href);
      if (safe) {
        links.push({ label: label, href: safe });
      }
    };
    const edges = edgesOf(state, id);
    if (node.type === "event") {
      if (node.date) {
        lines.push(
          formatDate(node.date) + (node.dated ? "" : " — дата публикации: своей даты у события нет")
        );
      }
      edges
        .filter(function (edge) {
          return edge.to === id && state.nodes.get(edge.from).type === "person";
        })
        .forEach(function (edge) {
          lines.push(state.nodes.get(edge.from).label + ": " + edge.label);
        });
      if (typeof node.confidence === "number") {
        lines.push("Уверенность извлечения: " + node.confidence.toFixed(2));
      }
      if (node.publication_source) {
        lines.push("Источник: " + node.publication_source);
      }
      if (node.publication_title) {
        lines.push("Публикация: " + node.publication_title);
      }
      edges
        .filter(function (edge) {
          return edge.from === id && edge.type !== "evidence";
        })
        .forEach(function (edge) {
          const other = state.nodes.get(edge.to);
          lines.push(
            TYPE_NAMES[other.type] + ": " + other.label + (edge.title ? " (" + edge.title + ")" : "")
          );
        });
      link("Открыть место в публикации", node.href);
    } else if (node.type === "person") {
      const mine = neighbours(state, id, "event");
      if (id !== state.center && state.center !== null) {
        const theirs = new Set(
          neighbours(state, state.center, "event").map(function (event) {
            return event.id;
          })
        );
        lines.push(
          "Общих событий в графе: " +
            mine.filter(function (event) {
              return theirs.has(event.id);
            }).length
        );
        edges
          .filter(function (edge) {
            return edge.type === COOCCURRENCE;
          })
          .forEach(function (edge) {
            lines.push(
              "Общих публикаций: " +
                edge.shared_publications +
                " — это только совместное упоминание, не установленная связь"
            );
          });
      }
      lines.push("Событий в графе: " + mine.length);
      link("Открыть досье", node.href);
    } else if (node.type === "publication") {
      if (node.publication_source) {
        lines.push("Источник: " + node.publication_source);
      }
      if (node.date) {
        lines.push(formatDate(node.date));
      }
      edges
        .filter(function (edge) {
          return edge.type === "evidence";
        })
        .forEach(function (edge) {
          link("Открыть место события: " + state.nodes.get(edge.from).label, edge.href);
        });
      link("Открыть публикацию", node.href);
    } else {
      lines.push("Событий в графе: " + neighbours(state, id, "event").length);
      if (node.type === "court" || node.type === "authority") {
        lines.push("Название — как в тексте публикации; справочника судов и органов нет");
        link("Публикации с этим названием", node.href);
      } else {
        link("Люди с этой статьёй", node.href);
      }
    }
    const cut = state.more.get(id) || {};
    Object.keys(cut).forEach(function (type) {
      lines.push(moreText(type, cut[type]));
    });
    return {
      title: String(node.label),
      kind: TYPE_NAMES[node.type] || node.type,
      lines: lines,
      links: links,
      expand:
        Boolean(node.expandable) && !state.expanded.has(id)
          ? node.type === "event"
            ? "Показать связи"
            : "Показать события"
          : null,
    };
  }

  return {
    COOCCURRENCE: COOCCURRENCE,
    DEFAULT_FILTERS: DEFAULT_FILTERS,
    GROUPS: GROUPS,
    createState: createState,
    details: details,
    formatDate: formatDate,
    hidden: hidden,
    merge: merge,
    moreText: moreText,
    safeHref: safeHref,
    visEdge: visEdge,
    visNode: visNode,
  };
});
