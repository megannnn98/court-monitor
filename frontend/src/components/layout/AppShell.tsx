import { useState } from "react";
import {
  BookOpen,
  ExternalLink,
  FileText,
  Info,
  List,
  type LucideIcon,
  Menu,
  MessageCircle,
  Newspaper,
  RefreshCw,
  Rows3,
  Scale,
  Search,
  SlidersHorizontal,
  Table,
  Users
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { StatusStrip } from "@/components/layout/StatusStrip";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useStatus } from "@/hooks/useStatus";
import { formatNumber } from "@/lib/format";
import { isMoved, NAV, type NavItem } from "@/lib/navigation";
import { cn } from "@/lib/utils";

// The legacy menu's icons, by the item's key (`src/web/ui/layout.py`, `_ICONS`).
const ICONS: Record<string, LucideIcon> = {
  cycle: RefreshCw,
  political: List,
  investigations: Search,
  ask: MessageCircle,
  entities: Users,
  publications: Newspaper,
  sentences: Scale,
  rfm: Rows3,
  airtable: Table,
  management: SlidersHorizontal,
  logs: FileText,
  wiki: BookOpen,
  about: Info
};

function Icon({ item }: { item: NavItem }) {
  const Shape = ICONS[item.key];
  return Shape ? <Shape className="size-4 shrink-0" aria-hidden="true" /> : null;
}

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
        <Icon item={item} />
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
      <Icon item={item} />
      <span>{item.label}</span>
      <Count item={item} />
      <ExternalLink className={item.count ? "size-3.5" : "ml-auto size-3.5"} aria-label="старый интерфейс" />
    </a>
  );
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Главное меню" className="flex min-h-0 flex-1 flex-col gap-4">
      {NAV.map((group, position) => (
        // The system's pages are the block kept at the bottom, as in the legacy menu.
        <div
          key={group.title}
          className={cn("space-y-1", position === NAV.length - 1 && "mt-auto border-t border-white/10 pt-3")}
        >
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
      <aside className="hidden bg-sidebar p-3 text-sidebar-foreground md:sticky md:top-0 md:flex md:h-screen md:flex-col md:overflow-y-auto">
        <div className="mb-4 px-3">
          <NavLink to="/work" className="block text-lg font-semibold text-white">
            court-monitor
          </NavLink>
          <span className="text-xs tracking-wide text-sidebar-muted uppercase">система расследований</span>
        </div>
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
        {/* `break-words`: a long address or a word without spaces in a text of the base wraps
            instead of making the page wider than a phone's screen. */}
        <main className="mx-auto max-w-6xl p-4 break-words md:p-6">
          <StatusStrip />
          <Outlet />
        </main>
      </div>
    </div>
  );
}
