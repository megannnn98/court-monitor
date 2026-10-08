import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { addOfficialV1, addSuggestedOfficialV1, deactivateOfficialV1, getOfficialsV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { unwrap } from "@/lib/api";
import { DASH, formatNumber } from "@/lib/format";
import { dossierPath } from "@/lib/navigation";

const SAID: Record<string, string> = {
  added: "Добавлено.",
  already: "Этот человек уже в списке.",
  deactivated: "Снят: строка осталась в списке, но больше не действует."
};

type Change = { action: "add"; full_name: string; category: string; reason: string } | { action: "suggested"; key: string } | { action: "deactivate"; external_id: string };

export function OfficialsPage() {
  const client = useQueryClient();
  const list = useQuery({ queryKey: ["officials"], queryFn: () => unwrap(getOfficialsV1()) });
  const [form, setForm] = useState({ full_name: "", category: "judge", reason: "" });
  const change = useMutation({
    mutationFn: (what: Change) =>
      what.action === "add"
        ? unwrap(addOfficialV1({ body: { full_name: what.full_name, category: what.category, reason: what.reason } }))
        : what.action === "suggested"
          ? unwrap(addSuggestedOfficialV1({ body: { key: what.key } }))
          : unwrap(deactivateOfficialV1({ body: { external_id: what.external_id } })),
    onSuccess: async (answer, what) => {
      if (what.action === "add" && answer.status === "added") {
        setForm({ full_name: "", category: form.category, reason: "" });
      }
      await client.invalidateQueries({ queryKey: ["officials"] });
    }
  });

  function add(event: FormEvent) {
    event.preventDefault();
    change.mutate({ action: "add", ...form });
  }

  return (
    <>
      <PageHeader
        title="Должностные лица"
        instruction="Люди, которых нельзя делать фигурантом: судьи, прокуроры, следователи, защитники, свидетели. Снимается кнопкой, а не удалением: решение остаётся в базе."
      />
      <QueryState query={list}>
        {(data) => (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <a className="text-sm underline" href="/ui/airtable/officials.csv">
                Скачать списком (CSV)
              </a>
              <Link className="text-sm underline" to="/airtable">
                База Airtable
              </Link>
            </div>
            <form onSubmit={add} aria-label="Добавить человека" className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-4">
              <div className="space-y-1">
                <Label htmlFor="official-name">ФИО</Label>
                <Input id="official-name" required maxLength={512} className="w-72" value={form.full_name} onChange={(event) => setForm({ ...form, full_name: event.target.value })} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="official-category">Категория</Label>
                <select id="official-category" className="h-9 rounded-md border bg-background px-2 text-sm" value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })}>
                  {data.categories.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="official-reason">Причина</Label>
                <Input id="official-reason" maxLength={255} className="w-64" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} />
              </div>
              <Button type="submit" disabled={change.isPending || !form.full_name.trim()}>
                Добавить
              </Button>
            </form>
            {change.data ? <p className="text-sm">{SAID[change.data.status] ?? change.data.status}</p> : null}
            {change.isError ? <p className="text-sm text-destructive">{change.error.message}</p> : null}
            <p className="text-sm text-muted-foreground">
              Всего в списке: {formatNumber(data.total)}. Судей, прокуроров и других, чьё звание стоит в текстах перед именем, шаг «Найти фигурантов» добавляет сам («автоматически» в причине).
            </p>

            {data.suggestions.length ? (
              <section aria-label="Предложения системы" className="space-y-2 rounded-lg border bg-card p-4">
                <h2 className="font-medium">Предложения системы: {data.suggestions.length}</h2>
                <p className="text-sm text-muted-foreground">
                  Модель решила, что эти люди — должностные лица, но перед именем в текстах должности нет. Это догадка: среди них бывают и обвиняемые. Добавьте тех, кто точно должностное лицо; в
                  силу это вступит при следующем «Найти фигурантов».
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Кто</TableHead>
                      <TableHead>Категория</TableHead>
                      <TableHead>Почему модель так думает</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.suggestions.map((item) => (
                      <TableRow key={item.key}>
                        <TableCell>
                          <Link className="underline" to={dossierPath(item.key)}>
                            {item.name}
                          </Link>
                        </TableCell>
                        <TableCell>{item.category}</TableCell>
                        <TableCell className="whitespace-normal text-xs">{item.reason}</TableCell>
                        <TableCell>
                          <Button size="sm" variant="outline" disabled={change.isPending} onClick={() => change.mutate({ action: "suggested", key: item.key })}>
                            Добавить в список
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>
            ) : null}

            <Table>
              <TableHeader>
                <TableRow>
                  {["ФИО в списке", "Кого нашли в статьях", "Категория", "Причина", "В силе", ""].map((name, index) => (
                    <TableHead key={index}>{name}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground">
                      Список пуст.
                    </TableCell>
                  </TableRow>
                ) : (
                  data.rows.map((row) => (
                    <TableRow key={row.external_id} className={row.active ? undefined : "opacity-60"}>
                      <TableCell>{row.full_name}</TableCell>
                      <TableCell>
                        {row.entity_name ? (
                          <Link className="underline" to={dossierPath(row.entity_key)}>
                            {row.entity_name}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">ещё не встречался в статьях</span>
                        )}
                      </TableCell>
                      <TableCell>{row.category}</TableCell>
                      <TableCell className="whitespace-normal">{row.reason || DASH}</TableCell>
                      <TableCell>{row.active ? "да" : "нет"}</TableCell>
                      <TableCell>
                        {row.active ? (
                          <Button size="sm" variant="outline" disabled={change.isPending} onClick={() => change.mutate({ action: "deactivate", external_id: row.external_id })}>
                            Снять
                          </Button>
                        ) : (
                          <span className="text-muted-foreground">снят</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </QueryState>
    </>
  );
}
