import { useQuery } from "@tanstack/react-query";

import { getStatusV1 } from "@/api/generated";
import { unwrap } from "@/lib/api";

/** The legacy status strip's numbers; one request shared by the strip and the menu. */
export function useStatus() {
  return useQuery({ queryKey: ["status"], queryFn: () => unwrap(getStatusV1()) });
}
