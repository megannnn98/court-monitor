import type { ReactNode } from "react";

export function PageHeader({ title, instruction, children }: { title: string; instruction?: string; children?: ReactNode }) {
  return (
    <header className="mb-4 space-y-1">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {instruction ? <p className="text-sm text-muted-foreground">{instruction}</p> : null}
      {children}
    </header>
  );
}
