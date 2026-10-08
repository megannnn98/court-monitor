import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { PageHeader } from "@/components/layout/PageHeader";
import { type Here, LegacyHtml } from "@/components/LegacyHtml";
import { QueryState } from "@/components/QueryState";
import { readAgainAfterDecision } from "@/lib/decisions";
import { readLegacyPage } from "@/lib/legacy";

/** Read again while the page shows a live run, as the legacy page reloads itself. */
export const POLL_MS = 5_000;

/** A page of the legacy console under the console's own menu: its name, its «Как это
 * работает» and what it shows, as the server renders it for the address's own query. The
 * page is the legacy one piece for piece — nothing of it is told twice here. */
export function LegacyPage({ legacy, path, title }: Here & { title: string }) {
  const client = useQueryClient();
  const { search } = useLocation();
  const here = { legacy, path };
  const page = useQuery({
    queryKey: ["legacy", legacy, search],
    queryFn: () => readLegacyPage(`${legacy}${search}`),
    refetchInterval: (query) => (query.state.data?.live ? POLL_MS : false)
  });

  return (
    <>
      <PageHeader title={page.data?.title || title} instruction={page.data?.instruction} />
      <QueryState query={page}>
        {(piece) => <LegacyHtml html={piece.html} here={here} onDone={() => readAgainAfterDecision(client, ["legacy"], ["operations", "runs"])} />}
      </QueryState>
    </>
  );
}
