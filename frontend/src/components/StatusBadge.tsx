import { Badge } from "@/components/ui/badge";
import { labelOf, toneOf, type Tone } from "@/lib/labels";
import { cn } from "@/lib/utils";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "",
  running: "border-amber-300 bg-amber-100 text-amber-900",
  good: "border-emerald-300 bg-emerald-100 text-emerald-900",
  bad: "border-red-300 bg-red-100 text-red-900"
};

export function StatusBadge({ status, labels }: { status: string; labels: Record<string, string> }) {
  return (
    <Badge variant="outline" className={cn(TONE_CLASSES[toneOf(status)])}>
      {labelOf(labels, status)}
    </Badge>
  );
}
