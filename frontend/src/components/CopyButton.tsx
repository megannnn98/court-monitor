import { useEffect, useState, type MouseEvent } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";

/** The text to the clipboard. On a page that is not served securely the browser gives no
 * clipboard to write to; a hidden field and the browser's own «copy» do the same. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Refused (no permission, no focus): the older way below.
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    field.remove();
  }
}

/** A small button beside a name that copies it as it is shown — what the old «Результат»
 * had beside every name, to paste the person into a search or a table. */
export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) {
      return;
    }
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  function copy(event: MouseEvent) {
    // Beside a name in a row: the click copies, it does not choose the row.
    event.stopPropagation();
    void copyText(text).then(setCopied);
  }

  return (
    <button
      type="button"
      onClick={copy}
      title={copied ? "Скопировано" : "Скопировать имя"}
      aria-label={`Скопировать: ${text}`}
      className={cn(
        "inline-flex size-6 shrink-0 items-center justify-center rounded align-middle text-muted-foreground hover:bg-muted hover:text-foreground",
        copied && "text-emerald-700",
        className
      )}
    >
      {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
    </button>
  );
}
