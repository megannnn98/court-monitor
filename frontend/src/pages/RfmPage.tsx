import { LegacyPage } from "@/components/LegacyPage";

/** «Перечень РФМ» of the legacy console: the operator's table of the list — who was
 * included when, our match, the periods and the Excel file. The page is the legacy one,
 * piece for piece (`LegacyPage`). */
export function RfmPage() {
  return <LegacyPage legacy="/ui/rfm" path="/rfm" title="Перечень РФМ" />;
}
