import { type ReactNode, useState } from "react";

import { useStatus } from "@/hooks/useStatus";

/** The page's name and, folded as in the legacy console, what the page is and what to do
 * next: at hand, not in the way. */
export function PageHeader({ title, instruction, children }: { title: string; instruction?: string; children?: ReactNode }) {
  const status = useStatus();
  const [open, setOpen] = useState(false);
  const next = status.data?.next_action;
  return (
    <header className="mb-4 space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {instruction ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="flex items-center gap-2 rounded-full border bg-card px-3 py-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <span aria-hidden="true" className="flex size-5 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              ?
            </span>
            Как это работает
          </button>
        ) : null}
      </div>
      {instruction && open ? (
        <div className="max-w-3xl space-y-1 rounded-md border bg-card p-3 text-sm">
          <p>{instruction}</p>
          {next ? (
            <p>
              <strong>Дальше:</strong> {next}
            </p>
          ) : null}
        </div>
      ) : null}
      {children}
    </header>
  );
}
