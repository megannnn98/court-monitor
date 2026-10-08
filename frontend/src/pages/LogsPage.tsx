import { LegacyPage } from "@/components/LegacyPage";

/** «Логи» of the legacy console: the run's journal and result while it runs and after,
 * its progress by sources, «Остановить», and the runs before. The page is the legacy one,
 * piece for piece (`LegacyPage`). */
export function LogsPage() {
  return <LegacyPage legacy="/ui/logs" path="/logs" title="Логи" />;
}
