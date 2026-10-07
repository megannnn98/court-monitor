import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { EventGraph } from "@/components/EventGraph";

const SCRIPTS = [
  "/static/vendor/vis-network/vis-network.min.js",
  "/static/investigation-graph-core.js",
  "/static/investigation-graph.js"
];

/** The scripts as if already on the page: the component then mounts at once. */
function scriptsLoaded() {
  for (const src of SCRIPTS) {
    const script = document.createElement("script");
    script.dataset.graphSrc = src;
    script.dataset.loaded = "1";
    document.body.append(script);
  }
}

const mounted: { box: HTMLElement; destroy: ReturnType<typeof vi.fn> }[] = [];

beforeEach(() => {
  mounted.length = 0;
  window.InvestigationGraph = {
    mount: (box: HTMLElement) => {
      const handle = { box, destroy: vi.fn() };
      mounted.push(handle);
      return handle;
    }
  };
});

afterEach(() => {
  document.querySelectorAll("script[data-graph-src]").forEach((script) => script.remove());
  delete window.InvestigationGraph;
});

it("draws one graph per person and destroys the one left behind", async () => {
  scriptsLoaded();
  const { rerender, unmount } = render(<EventGraph key="a" graphUrl="/api/investigations/a/graph" name="A" />);
  await waitFor(() => expect(mounted).toHaveLength(1));
  expect(mounted[0].box.dataset.graphUrl).toBe("/api/investigations/a/graph");
  expect(mounted[0].box.dataset.mount).toBe("manual");

  // Another dossier: the caller keys the graph by the person.
  rerender(<EventGraph key="b" graphUrl="/api/investigations/b/graph" name="B" />);
  await waitFor(() => expect(mounted).toHaveLength(2));
  expect(mounted[0].destroy).toHaveBeenCalledOnce();
  expect(mounted[1].box.dataset.graphUrl).toBe("/api/investigations/b/graph");
  expect(mounted[1].box).not.toBe(mounted[0].box);

  unmount();
  expect(mounted[1].destroy).toHaveBeenCalledOnce();
});

it("draws nothing for a dossier left before the scripts came", async () => {
  // The scripts are still loading: nothing is marked loaded.
  const { unmount } = render(<EventGraph graphUrl="/api/investigations/a/graph" name="A" />);
  unmount();
  for (const script of document.querySelectorAll<HTMLScriptElement>("script[data-graph-src]")) {
    script.dataset.loaded = "1";
    script.dispatchEvent(new Event("load"));
  }

  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(mounted).toHaveLength(0);
});
