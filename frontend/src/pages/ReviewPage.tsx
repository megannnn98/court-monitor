import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  decidePoliticsV1,
  decideRoleV1,
  listUnclearPoliticsV1,
  listUnclearRolesV1,
  type ReviewItemResponse,
  type ReviewListResponse
} from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { unwrap } from "@/lib/api";
import { readAgainAfterDecision } from "@/lib/decisions";
import { dossierPath } from "@/lib/navigation";

type Station = {
  queryKey: string[];
  list: () => Promise<ReviewListResponse>;
  decide: (key: string, choice: string) => Promise<unknown>;
};

const STATIONS: Record<"roles" | "politics", Station> = {
  roles: {
    queryKey: ["review", "roles"],
    list: () => unwrap(listUnclearRolesV1()),
    decide: (key, role) => unwrap(decideRoleV1({ body: { key, role } }))
  },
  politics: {
    queryKey: ["review", "politics"],
    list: () => unwrap(listUnclearPoliticsV1()),
    decide: (key, verdict) => unwrap(decidePoliticsV1({ body: { key, verdict } }))
  }
};

function Decision({ station, item, choices }: { station: Station; item: ReviewItemResponse; choices: ReviewListResponse["choices"] }) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (choice: string) => station.decide(item.key, choice),
    onSuccess: async () => {
      await readAgainAfterDecision(client, station.queryKey);
    }
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      {choices.map((choice, index) => (
        <Button
          key={choice.value}
          size="sm"
          variant={index === 0 ? "default" : "outline"}
          disabled={decide.isPending}
          onClick={() => decide.mutate(choice.value)}
        >
          {choice.label}
        </Button>
      ))}
      {decide.isError ? <span className="text-xs text-destructive">{decide.error.message}</span> : null}
    </div>
  );
}

/** A review station: the people a step could not decide, one decision per row. The
 * decision is applied at once and kept for every rebuild (the server's rule). */
export function ReviewPage({ kind }: { kind: "roles" | "politics" }) {
  const station = STATIONS[kind];
  const review = useQuery({ queryKey: station.queryKey, queryFn: station.list });

  return (
    <QueryState query={review}>
      {(data) => (
        <>
          <PageHeader title={data.title} instruction={data.explanation} />
          {data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">{data.title}: открытых случаев нет.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Человек</TableHead>
                  <TableHead>Почему не решено</TableHead>
                  <TableHead>Решение</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((item) => (
                  <TableRow key={item.key}>
                    <TableCell>
                      <Link className="underline" to={dossierPath(item.key)}>
                        {item.name}
                      </Link>
                    </TableCell>
                    <TableCell className="whitespace-normal">{item.reason}</TableCell>
                    <TableCell>
                      <Decision station={station} item={item} choices={data.choices} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </>
      )}
    </QueryState>
  );
}
