import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  getMonitoringStatusV1,
  listMonitoringFindingsV1,
  listMonitoringRunsV1,
  type MonitoringRunStatus,
  type MonitoringRunView
} from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { Pager } from "@/components/Pager";
import { isEmptyList, QueryState } from "@/components/QueryState";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useUrlState } from "@/hooks/useUrlState";
import { unwrap } from "@/lib/api";
import { DASH, formatDateTime, formatDuration, formatNumber } from "@/lib/format";
import { FINDING_STATUS, labelOf, MONITORING_RUN_STATUS, TRIGGER } from "@/lib/labels";

const PAGE_SIZE = 50;
const ALL = "all";
const RUN_STATUSES: MonitoringRunStatus[] = ["running", "completed", "completed_with_errors", "failed", "aborted"];

function RunsTable({ runs }: { runs: MonitoringRunView[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Запуск</TableHead>
          <TableHead>Источник</TableHead>
          <TableHead>Начат</TableHead>
          <TableHead>Длительность</TableHead>
          <TableHead>Статус</TableHead>
          <TableHead>Найдено</TableHead>
          <TableHead>Загружено</TableHead>
          <TableHead>Статей</TableHead>
          <TableHead>Ошибок</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow key={run.id}>
            <TableCell>
              #{run.id} <span className="text-muted-foreground">{labelOf(TRIGGER, run.trigger_type)}</span>
            </TableCell>
            <TableCell>{run.source ?? run.scope}</TableCell>
            <TableCell>{formatDateTime(run.started_at)}</TableCell>
            <TableCell>{formatDuration(run.duration_seconds)}</TableCell>
            <TableCell>
              <StatusBadge status={run.status} labels={MONITORING_RUN_STATUS} />
            </TableCell>
            <TableCell>{formatNumber(run.documents_discovered)}</TableCell>
            <TableCell>{formatNumber(run.documents_ingested)}</TableCell>
            <TableCell>{formatNumber(run.articles_extracted)}</TableCell>
            <TableCell title={run.error_message ?? undefined}>{formatNumber(run.error_count)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Summary() {
  const url = useUrlState();
  const status = useQuery({ queryKey: ["monitoring", "status"], queryFn: () => unwrap(getMonitoringStatusV1()) });
  return (
    <QueryState query={status}>
      {(view) => (
        <div className="mb-6 space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Идут сейчас</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold">{formatNumber(view.running?.length ?? 0)}</CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Активные находки</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold">{formatNumber(view.active_findings ?? 0)}</CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Источников</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold">{formatNumber(view.sources?.length ?? 0)}</CardContent>
            </Card>
          </div>
          {view.sources?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Источник</TableHead>
                  <TableHead>Последний успешный</TableHead>
                  <TableHead>Последний обход</TableHead>
                  <TableHead>Найдено в нём</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {view.sources.map((source) => (
                  <TableRow key={source.source_name}>
                    <TableCell>
                      <button
                        type="button"
                        className="underline"
                        onClick={() => url.set({ tab: null, source: source.source_name, runs_page: null })}
                      >
                        {source.source_name}
                      </button>
                    </TableCell>
                    <TableCell>
                      {source.last_successful_run_id ? `#${source.last_successful_run_id}, ` : ""}
                      {formatDateTime(source.last_successful_run_at)}
                    </TableCell>
                    <TableCell>{formatDateTime(source.last_discovered_at)}</TableCell>
                    <TableCell>{formatNumber(source.last_discovered_count)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">Состояния источников нет.</p>
          )}
        </div>
      )}
    </QueryState>
  );
}

function Runs() {
  const url = useUrlState();
  const page = Math.max(1, url.getNumber("runs_page", 1));
  const status = url.get("status");
  const source = url.get("source");
  const runs = useQuery({
    queryKey: ["monitoring", "runs", page, status, source],
    queryFn: () =>
      unwrap(
        listMonitoringRunsV1({
          query: {
            limit: PAGE_SIZE,
            offset: (page - 1) * PAGE_SIZE,
            status: (status || null) as MonitoringRunStatus | null,
            source: source || null
          }
        })
      )
  });
  return (
    <>
      <div className="mb-3 space-y-1">
        <Label>Статус</Label>
        <Select value={status || ALL} onValueChange={(value) => url.set({ status: value === ALL ? null : value, runs_page: null })}>
          <SelectTrigger className="w-56" aria-label="Статус запуска">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Все</SelectItem>
            {RUN_STATUSES.map((code) => (
              <SelectItem key={code} value={code}>
                {labelOf(MONITORING_RUN_STATUS, code)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {source ? (
          <p className="text-sm">
            Источник: {source}{" "}
            <button type="button" className="underline" onClick={() => url.set({ source: null })}>
              сбросить
            </button>
          </p>
        ) : null}
      </div>
      <QueryState query={runs} isEmpty={(rows) => isEmptyList(rows) && page === 1} empty="Запусков мониторинга нет.">
        {(rows) => (
          <>
            <RunsTable runs={rows} />
            <Pager page={page} pageSize={PAGE_SIZE} shown={rows.length} onPage={(next) => url.set({ runs_page: next === 1 ? null : next })} />
          </>
        )}
      </QueryState>
    </>
  );
}

function Findings() {
  const url = useUrlState();
  const page = Math.max(1, url.getNumber("findings_page", 1));
  const all = url.get("findings") === "all";
  const findings = useQuery({
    queryKey: ["monitoring", "findings", page, all],
    queryFn: () =>
      unwrap(listMonitoringFindingsV1({ query: { active_only: !all, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE } }))
  });
  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <Checkbox
          id="all-findings"
          checked={all}
          onCheckedChange={(checked) => url.set({ findings: checked === true ? "all" : null, findings_page: null })}
        />
        <Label htmlFor="all-findings">Показывать и неактивные</Label>
      </div>
      <QueryState query={findings} isEmpty={(rows) => isEmptyList(rows) && page === 1} empty="Находок нет.">
        {(rows) => (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>Тип</TableHead>
                  <TableHead>Персона</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead>Впервые</TableHead>
                  <TableHead>Последний раз</TableHead>
                  <TableHead>Неактивна с</TableHead>
                  <TableHead>Критерии</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((finding) => (
                  <TableRow key={finding.id}>
                    <TableCell>{finding.id}</TableCell>
                    <TableCell>{finding.finding_type}</TableCell>
                    <TableCell>
                      <Link className="underline" to={`/persons/${finding.person_id}`}>
                        {finding.person_id}
                      </Link>
                    </TableCell>
                    <TableCell>{labelOf(FINDING_STATUS, finding.status)}</TableCell>
                    <TableCell>{formatDateTime(finding.first_seen_at)}</TableCell>
                    <TableCell>{formatDateTime(finding.last_seen_at)}</TableCell>
                    <TableCell>{finding.inactive_since ? formatDateTime(finding.inactive_since) : DASH}</TableCell>
                    <TableCell>{finding.criteria_version}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pager page={page} pageSize={PAGE_SIZE} shown={rows.length} onPage={(next) => url.set({ findings_page: next === 1 ? null : next })} />
          </>
        )}
      </QueryState>
    </>
  );
}

export function MonitoringPage() {
  const url = useUrlState();
  const tab = url.get("tab", "runs");
  return (
    <>
      <PageHeader title="Мониторинг" instruction="Обходы источников, их итог и найденные случаи." />
      <Summary />
      <Tabs value={tab} onValueChange={(value) => url.set({ tab: value === "runs" ? null : value })}>
        <TabsList>
          <TabsTrigger value="runs">Запуски</TabsTrigger>
          <TabsTrigger value="findings">Находки</TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          <Runs />
        </TabsContent>
        <TabsContent value="findings">
          <Findings />
        </TabsContent>
      </Tabs>
    </>
  );
}
