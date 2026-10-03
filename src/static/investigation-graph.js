// The interactive graph of a dossier: the person, their events, and what each event names.
//
// A viewer, not an editor: zoom, pan, drag, tap a node to read it, open it to load its
// neighbours. Every string that comes from the database reaches the page through
// `textContent` or as a plain value given to vis-network — never as HTML. Without this
// script (or without the library) the dossier is whole: the section says so and the rest
// of the page does not depend on it.
(function () {
  "use strict";

  const box = document.getElementById("investigation-graph");
  const core = window.InvestigationGraphCore;
  if (!box || !core) {
    return;
  }
  const canvas = box.querySelector(".ig-canvas");
  const status = box.querySelector(".ig-status");
  const panel = box.querySelector(".ig-panel");
  const say = function (message) {
    status.textContent = message;
  };
  if (typeof window.vis === "undefined" || !window.vis.Network) {
    say("Библиотека графа не загрузилась. Остальное досье от неё не зависит.");
    return;
  }

  const state = core.createState();
  const filters = Object.assign({}, core.DEFAULT_FILTERS);
  const nodes = new window.vis.DataSet([]);
  const edges = new window.vis.DataSet([]);
  let selected = null;

  // The colours are the theme's: read from the page, so the dark theme is followed.
  const style = getComputedStyle(document.documentElement);
  const colour = function (name, fallback) {
    return style.getPropertyValue(name).trim() || fallback;
  };
  const palette = {
    person: colour("--node-person", "#1e5f8f"),
    event: colour("--node-event", "#0f766e"),
    publication: colour("--node-publication", "#475569"),
    org: colour("--node-org", "#b45309"),
    article: colour("--node-article", "#7c3aed"),
  };
  const text = colour("--text", "#172033");
  const line = colour("--border-strong", "#b8c2d1");
  const surface = colour("--surface", "#ffffff");

  function drawn(node) {
    const shape = core.visNode(node, state.center);
    const fill = palette[shape.colour];
    return {
      id: shape.id,
      label: shape.label,
      shape: shape.shape,
      size: shape.size,
      borderWidth: shape.borderWidth,
      shapeProperties: { borderDashes: shape.dashed && !state.expanded.has(node.id) ? [4, 3] : false },
      color: {
        background: fill,
        border: shape.dashed && !state.expanded.has(node.id) ? text : fill,
        highlight: { background: fill, border: text },
      },
      font: { color: text, size: 12, strokeWidth: 3, strokeColor: surface },
    };
  }

  function drawnEdge(edge) {
    const shape = core.visEdge(edge);
    return {
      id: shape.id,
      from: shape.from,
      to: shape.to,
      label: shape.label,
      title: shape.title,
      dashes: shape.dashes,
      arrows: shape.arrows,
      color: { color: line, highlight: text },
      font: { color: text, size: 10, strokeWidth: 3, strokeColor: surface, align: "middle" },
    };
  }

  function applyFilters() {
    const off = core.hidden(state, filters);
    nodes.update(
      Array.from(state.nodes.keys()).map(function (id) {
        return { id: id, hidden: off.nodes.has(id) };
      })
    );
    edges.update(
      Array.from(state.edges.keys()).map(function (id) {
        return { id: id, hidden: off.edges.has(id) };
      })
    );
  }

  function add(payload) {
    const added = core.merge(state, payload);
    nodes.add(added.nodes.map(drawn));
    edges.add(added.edges.map(drawnEdge));
    applyFilters();
    return added;
  }

  function element(tag, className, content) {
    const made = document.createElement(tag);
    if (className) {
      made.className = className;
    }
    if (content !== undefined) {
      made.textContent = content;
    }
    return made;
  }

  function show(id) {
    selected = id;
    const told = id === null ? null : core.details(state, id);
    panel.replaceChildren();
    if (told === null) {
      panel.appendChild(element("p", "muted", "Нажмите на узел, чтобы прочитать его."));
      return;
    }
    panel.appendChild(element("p", "ig-kind", told.kind));
    panel.appendChild(element("h3", "", told.title));
    told.lines.forEach(function (words) {
      panel.appendChild(element("p", "", words));
    });
    const actions = element("p", "ig-actions");
    told.links.forEach(function (link) {
      const anchor = element("a", "button secondary", link.label);
      anchor.href = link.href;
      actions.appendChild(anchor);
    });
    if (told.expand) {
      const button = element("button", "", told.expand);
      button.type = "button";
      button.addEventListener("click", function () {
        open(id);
      });
      actions.appendChild(button);
    }
    panel.appendChild(actions);
  }

  function load(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (response) {
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      return response.json();
    });
  }

  function open(id) {
    const node = state.nodes.get(id);
    if (!node || !node.expandable || state.expanded.has(id)) {
      return;
    }
    say("Загружаю связи…");
    load(box.dataset.expandUrl + "?node=" + encodeURIComponent(id))
      .then(function (payload) {
        state.expanded.add(id);
        const added = add(payload);
        nodes.update(drawn(node));
        say(
          added.nodes.length
            ? "Добавлено узлов: " + added.nodes.length + "."
            : "Новых узлов нет: всё уже на графе."
        );
        show(id);
      })
      .catch(function () {
        say("Не удалось загрузить связи. Попробуйте ещё раз.");
      });
  }

  const network = new window.vis.Network(
    canvas,
    { nodes: nodes, edges: edges },
    {
      autoResize: true,
      // The same dossier opens to the same picture.
      layout: { randomSeed: 7 },
      interaction: { hover: true, tooltipDelay: 200, multiselect: false },
      physics: {
        solver: "forceAtlas2Based",
        forceAtlas2Based: { gravitationalConstant: -60, springLength: 110, avoidOverlap: 0.6 },
        stabilization: { iterations: 150 },
      },
      edges: { smooth: { type: "continuous" }, width: 1.2 },
      nodes: { shadow: false },
    }
  );
  network.on("click", function (params) {
    show(params.nodes.length ? params.nodes[0] : null);
  });
  network.on("doubleClick", function (params) {
    if (params.nodes.length) {
      open(params.nodes[0]);
    }
  });

  box.querySelectorAll("input[data-filter]").forEach(function (input) {
    filters[input.dataset.filter] = input.checked;
    input.addEventListener("change", function () {
      filters[input.dataset.filter] = input.checked;
      applyFilters();
      if (selected !== null && core.hidden(state, filters).nodes.has(selected)) {
        network.unselectAll();
        show(null);
      }
    });
  });
  box.querySelector(".ig-reset").addEventListener("click", function () {
    network.stabilize(150);
    network.fit({ animation: false });
  });

  say("Загружаю граф…");
  load(box.dataset.graphUrl)
    .then(function (payload) {
      // The first layout is shown whole; after that the reader's own zoom is kept.
      network.once("stabilized", function () {
        network.fit({ animation: false });
      });
      add(payload);
      state.expanded.add(state.center);
      nodes.update(drawn(state.nodes.get(state.center)));
      const cut = state.more.get(state.center) || {};
      const cuts = Object.keys(cut).map(function (type) {
        return core.moreText(type, cut[type]);
      });
      const events = payload.nodes.filter(function (node) {
        return node.type === "event";
      }).length;
      say(
        events
          ? "Событий на графе: " +
              events +
              (cuts.length ? " (" + cuts.join(", ") + ")" : "") +
              ". Нажмите на событие и откройте его связи."
          : "Человек не назван ни в одном событии: связывать не с чем."
      );
      show(state.center);
    })
    .catch(function () {
      say("Не удалось загрузить граф. Остальное досье от него не зависит.");
    });
})();
