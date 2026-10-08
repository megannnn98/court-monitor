import { LegacyPage } from "@/components/LegacyPage";

/** «Приговоры» of the legacy console: the cases with the articles' own words, «Неверно»
 * and the list of what was taken out, to be put back. The page is the legacy one,
 * piece for piece (`LegacyPage`). */
export function SentencesPage() {
  return <LegacyPage legacy="/ui/sentences" path="/sentences" title="Приговоры" />;
}
