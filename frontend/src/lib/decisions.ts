import type { QueryClient, QueryKey } from "@tanstack/react-query";

/** What every operator decision changes besides its own page: the counters of the status
 * strip and the menu, and «Работа» (its queues). One list, so an action added later
 * cannot forget one of them. */
export const DECIDED_ELSEWHERE: QueryKey[] = [["status"], ["cycle"], ["legacy"]];

export async function readAgainAfterDecision(client: QueryClient, ...own: QueryKey[]): Promise<void> {
  await Promise.all([...own, ...DECIDED_ELSEWHERE].map((queryKey) => client.invalidateQueries({ queryKey })));
}
