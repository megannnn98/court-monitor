import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** Filters, sorting and the page live in the address: a link shares them and «назад»
 * goes back to them. An empty value or the default leaves the address clean. */
export function useUrlState() {
  const [params, setParams] = useSearchParams();

  const get = useCallback((name: string, fallback = ""): string => params.get(name) ?? fallback, [params]);

  const getNumber = useCallback(
    (name: string, fallback: number): number => {
      const raw = params.get(name);
      const value = raw === null || raw === "" ? Number.NaN : Number(raw);
      return Number.isFinite(value) ? value : fallback;
    },
    [params]
  );

  const set = useCallback(
    (changes: Record<string, string | number | boolean | null | undefined>) => {
      setParams((current) => {
        const next = new URLSearchParams(current);
        for (const [name, value] of Object.entries(changes)) {
          if (value === null || value === undefined || value === "" || value === false) {
            next.delete(name);
          } else {
            next.set(name, String(value));
          }
        }
        return next;
      });
    },
    [setParams]
  );

  return { get, getNumber, set };
}
