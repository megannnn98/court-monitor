import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export type Option = { value: string; label: string; count?: number | null };

type Props = {
  label: string;
  value: string;
  /** The server's choices and words; a count, when given, follows the label. */
  options: Option[];
  onChange: (value: string) => void;
  className?: string;
};

export function OptionSelect({ label, value, options, onChange, className = "w-56" }: Props) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className={className} aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
              {option.count === null || option.count === undefined ? "" : ` (${option.count})`}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
