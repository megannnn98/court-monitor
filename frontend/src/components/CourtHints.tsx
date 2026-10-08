import type { CourtHintResponse } from "@/api/generated";

/** «Где судят по этой статье из этого места»: the courts the base saw sentence there. */
export function CourtHints({ courts, total }: { courts: CourtHintResponse[]; total: number }) {
  if (!courts.length) {
    return null;
  }
  return (
    <p className="text-sm text-muted-foreground">
      Где судят по этой статье из этого места (приговоры в базе Airtable, всего {total}):{" "}
      {courts.map((hint, index) => (
        <span key={hint.court}>
          {index ? "; " : ""}
          {hint.court} — {hint.cases}
          {hint.site ? (
            <>
              {" ("}
              <a className="underline" href={hint.site} rel="noopener noreferrer" target="_blank">
                сайт суда
              </a>
              {")"}
            </>
          ) : null}
        </span>
      ))}
      .
    </p>
  );
}
