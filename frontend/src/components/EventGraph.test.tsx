import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { EventGraph, forgetScripts, loadOnce } from "@/components/EventGraph";

const SCRIPTS = [
  "/static/vendor/vis-network/vis-network.min.js",
  "/static/investigation-graph-core.js",
  "/static/investigation-graph.js"
];

function script(src: string) {
  return document.querySelector<HTMLScriptElement>(`script[data-graph-src="${src}"]`);
}

/** The browser's answer for each script in turn: they load one after another. */
async function answer(...events: ("load" | "error")[]) {
  for (const [index, event] of events.entries()) {
    await waitFor(() => expect(script(SCRIPTS[index])).toBeTruthy());
    script(SCRIPTS[index])!.dispatchEvent(new Event(event));
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
  document.querySelectorAll("script[data-graph-src]").forEach((element) => element.remove());
  forgetScripts();
  delete window.InvestigationGraph;
});

it("draws one graph per person and destroys the one left behind", async () => {
  const { rerender, unmount } = render(<EventGraph key="a" graphUrl="/api/investigations/a/graph" name="A" />);
  await answer("load", "load", "load");
  await waitFor(() => expect(mounted).toHaveLength(1));
  expect(mounted[0].box.dataset.graphUrl).toBe("/api/investigations/a/graph");
  expect(mounted[0].box.dataset.mount).toBe("manual");

  // Another dossier: the caller keys the graph by the person; the scripts are not loaded again.
  rerender(<EventGraph key="b" graphUrl="/api/investigations/b/graph" name="B" />);
  await waitFor(() => expect(mounted).toHaveLength(2));
  expect(document.querySelectorAll("script[data-graph-src]")).toHaveLength(SCRIPTS.length);
  expect(mounted[0].destroy).toHaveBeenCalledOnce();
  expect(mounted[1].box.dataset.graphUrl).toBe("/api/investigations/b/graph");
  expect(mounted[1].box).not.toBe(mounted[0].box);

  unmount();
  expect(mounted[1].destroy).toHaveBeenCalledOnce();
});

it("draws nothing for a dossier left before the scripts came", async () => {
  const { unmount } = render(<EventGraph graphUrl="/api/investigations/a/graph" name="A" />);
  unmount();
  await answer("load", "load", "load");

  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(mounted).toHaveLength(0);
});

it("tells every waiting graph of a failed script, and the next one tries again", async () => {
  const first = loadOnce(SCRIPTS[0]);
  const second = loadOnce(SCRIPTS[0]);
  await answer("error");
  await expect(first).rejects.toThrow(SCRIPTS[0]);
  await expect(second).rejects.toThrow(SCRIPTS[0]);
  expect(script(SCRIPTS[0])).toBeNull();

  const again = loadOnce(SCRIPTS[0]);
  await answer("load");
  await expect(again).resolves.toBeUndefined();
});

it("says the graph is missing when its library fails", async () => {
  render(<EventGraph graphUrl="/api/investigations/a/graph" name="A" />);
  await answer("error");

  expect(await screen.findByText(/Библиотека графа не загрузилась/)).toBeTruthy();
  expect(mounted).toHaveLength(0);
});
