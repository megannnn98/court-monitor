import type { OptionResponse } from "@/api/generated";
import { cn } from "@/lib/utils";

/** The tabs of a page of cards, each with its count; the chosen one pressed. */
export function StatusTabs({ options, value, onChange }: { options: OptionResponse[]; value: string; onChange: (value: string) => void }) {
  return (
    <nav aria-label="Разделы" className="mb-3 flex flex-wrap border-b">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "-mb-px border-b-2 px-4 py-2 text-sm",
              active ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            {option.label}
            <span className="ml-1 rounded-full bg-muted px-2 text-xs">{option.count ?? 0}</span>
          </button>
        );
      })}
    </nav>
  );
}
