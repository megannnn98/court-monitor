import { useState } from "react";
import { ExternalLink, Menu } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { isMoved, NAV, type NavItem } from "@/lib/navigation";
import { cn } from "@/lib/utils";

const ITEM = "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground";

function Item({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  if (isMoved(item)) {
    return (
      <NavLink
        to={item.path}
        onClick={onNavigate}
        className={({ isActive }) => cn(ITEM, isActive && "bg-accent font-medium text-accent-foreground")}
      >
        {item.label}
      </NavLink>
    );
  }
  if (item.legacy === null) {
    return null;
  }
  // Not moved yet: the legacy page, marked so the jump is not a surprise.
  return (
    <a href={item.legacy} className={cn(ITEM, "text-muted-foreground")} title="Старый интерфейс">
      <span>{item.label}</span>
      <ExternalLink className="ml-auto size-3.5" aria-label="старый интерфейс" />
    </a>
  );
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Главное меню" className="space-y-4">
      {NAV.map((group) => (
        <div key={group.title} className="space-y-1">
          <div className="px-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group.title}</div>
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
      <aside className="hidden border-r p-3 md:block">
        <div className="mb-4 px-3 text-lg font-semibold">court-monitor</div>
        <Navigation />
      </aside>
      <div className="min-w-0">
        <header className="flex items-center gap-2 border-b px-4 py-2 md:hidden">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Меню">
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 overflow-y-auto p-3">
              <SheetHeader className="px-3">
                <SheetTitle>court-monitor</SheetTitle>
              </SheetHeader>
              <Navigation onNavigate={() => setOpen(false)} />
            </SheetContent>
          </Sheet>
          <span className="font-semibold">court-monitor</span>
        </header>
        <main className="mx-auto max-w-6xl p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
