import { LegacyPage } from "@/components/LegacyPage";

/** «Журнал запусков» of the legacy console: the steps to start, the run's card with what it
 * found and what it spent, the runs before, the sources' errors and the funnel. The page is the legacy one,
 * piece for piece (`LegacyPage`). */
export function RunsPage() {
  return <LegacyPage legacy="/ui/runs" path="/runs" title="Журнал запусков" />;
}
