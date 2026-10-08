import { useEffect, useState } from "react";

/** Whether the screen answers a media query now, and as it changes (a phone turned, a
 * window narrowed). `fallback` where the browser cannot tell. */
export function useMediaQuery(query: string, fallback = false): boolean {
  const read = () => (typeof window.matchMedia === "function" ? window.matchMedia(query).matches : fallback);
  const [matches, setMatches] = useState(read);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia(query);
    const changed = () => setMatches(media.matches);
    changed();
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, [query]);

  return matches;
}
