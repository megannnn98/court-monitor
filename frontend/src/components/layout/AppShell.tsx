import { useState } from "react";
import { ExternalLink, Menu } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { StatusStrip } from "@/components/layout/StatusStrip";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useStatus } from "@/hooks/useStatus";
import { formatNumber } from "@/lib/format";
import { isMoved, NAV, type NavItem } from "@/lib/navigation";
import { cn } from "@/lib/utils";

// The dark sidebar of the legacy console.
const ITEM = "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm text-sidebar-foreground hover:bg-sidebar-hover hover:text-white";

function Count({ item }: { item: NavItem }) {
  const status = useStatus();
  if (!item.count || !status.data) {
    return null;
  }
  const value = item.count === "queue" ? status.data.queue.total : status.data.result;
  return <span className="ml-auto rounded-full bg-white/10 px-1.5 text-xs text-sidebar-foreground">{formatNumber(value)}</span>;
}

function Item({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  if (isMoved(item)) {
    return (
      <NavLink
        to={item.path}
        onClick={onNavigate}
        className={({ isActive }) => cn(ITEM, isActive && "bg-sidebar-hover font-medium text-white")}
      >
        {item.label}
        <Count item={item} />
      </NavLink>
    );
  }
  if (item.legacy === null) {
    return null;
  }
  // Not moved yet: the legacy page, marked so the jump is not a surprise.
  return (
    <a href={item.legacy} className={cn(ITEM, "text-sidebar-muted")} title="Старый интерфейс">
      <span>{item.label}</span>
      <Count item={item} />
      <ExternalLink className={item.count ? "size-3.5" : "ml-auto size-3.5"} aria-label="старый интерфейс" />
    </a>
  );
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Главное меню" className="space-y-4">
      {NAV.map((group) => (
        <div key={group.title} className="space-y-1">
          <div className="px-3 text-xs font-semibold uppercase tracking-wide text-sidebar-muted">{group.title}</div>
          {group.items.map((item) => (
            <Item key={item.key} item={item} onNavigate={onNavigate} />
          ))}
        </div>
      ))}
    </nav>
  );
}

export function AppShell() {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-screen bg-background text-foreground md:grid md:grid-cols-[15rem_1fr]">
      <aside className="hidden bg-sidebar p-3 text-sidebar-foreground md:block">
        <div className="mb-4 px-3 text-lg font-semibold text-white">court-monitor</div>
        <Navigation />
      </aside>
      <div className="min-w-0">
        <header className="flex items-center gap-2 border-b bg-card px-4 py-2 md:hidden">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Меню">
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 overflow-y-auto border-none bg-sidebar p-3 text-sidebar-foreground">
              <SheetHeader className="px-3">
                <SheetTitle className="text-white">court-monitor</SheetTitle>
              </SheetHeader>
              <Navigation onNavigate={() => setOpen(false)} />
            </SheetContent>
          </Sheet>
          <span className="font-semibold">court-monitor</span>
        </header>
        <main className="mx-auto max-w-6xl p-4 md:p-6">
          <StatusStrip />
          <Outlet />
        </main>
      </div>
    </div>
  );
}
