import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Bot, ListChecks, RefreshCw } from "lucide-react";
import {
  api,
  type OrchestratorCurrentResponse,
  type OrchestratorShard,
  type OrchestratorStage,
} from "@/lib/api";
import { formatTokenCount } from "@/lib/format";
import { isoTimeAgo } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STATUS_VARIANTS: Record<string, "success" | "warning" | "destructive" | "outline" | "secondary"> = {
  complete: "success",
  degraded_complete: "warning",
  failed: "destructive",
  running: "secondary",
  retrying: "warning",
  pending: "outline",
  completed: "success",
  idle: "outline",
};

function shardStatusVariant(status: string) {
  return STATUS_VARIANTS[status] ?? "outline";
}

function preview(text: string, limit = 180) {
  const trimmed = (text || "").trim().replace(/\s+/g, " ");
  if (!trimmed) return "No task text recorded.";
  return trimmed.length > limit ? `${trimmed.slice(0, limit - 1)}…` : trimmed;
}

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">{label}</CardTitle>
        <Activity className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="font-display truncate text-2xl font-bold" title={value}>
          {value}
        </div>
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}

function StageCard({ stage }: { stage: OrchestratorStage }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-sm capitalize">{stage.role}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              planned workers {stage.planned_workers} · provider {stage.provider_family || "n/a"}
            </p>
          </div>
          <Badge variant={shardStatusVariant(stage.status)}>{stage.status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded border border-border/60 p-2">
          <div className="text-muted-foreground">Completed</div>
          <div className="mt-1 font-semibold">{stage.completed_shards}</div>
        </div>
        <div className="rounded border border-border/60 p-2">
          <div className="text-muted-foreground">Failed</div>
          <div className="mt-1 font-semibold">{stage.failed_shards}</div>
        </div>
        <div className="rounded border border-border/60 p-2">
          <div className="text-muted-foreground">Retrying</div>
          <div className="mt-1 font-semibold">{stage.retrying_shards}</div>
        </div>
        <div className="rounded border border-border/60 p-2">
          <div className="text-muted-foreground">Pending</div>
          <div className="mt-1 font-semibold">{stage.pending_shards}</div>
        </div>
      </CardContent>
    </Card>
  );
}

function ShardRow({ shard }: { shard: OrchestratorShard }) {
  return (
    <tr className="border-b border-border/60 align-top hover:bg-muted/30">
      <td className="px-3 py-3 text-xs font-mono-ui text-muted-foreground">{shard.role}</td>
      <td className="px-3 py-3 text-xs font-mono-ui">{shard.shard_index}</td>
      <td className="min-w-[260px] px-3 py-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Badge variant={shardStatusVariant(shard.status)} className="text-[10px] uppercase">
              {shard.status}
            </Badge>
            <span className="text-xs text-muted-foreground">attempts {shard.attempts || 0}</span>
          </div>
          <div className="text-sm font-medium">{shard.title || `shard ${shard.shard_index}`}</div>
          <div className="whitespace-pre-wrap text-xs text-muted-foreground">
            {preview(shard.instruction)}
          </div>
        </div>
      </td>
      <td className="px-3 py-3 text-xs">
        <div className="flex flex-col gap-1">
          <span className="font-medium">{shard.provider_id || "n/a"}</span>
          <span className="text-muted-foreground">{shard.credential_label || "n/a"}</span>
          <span className="text-muted-foreground">{shard.model || "n/a"}</span>
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-3 text-right text-xs">
        <div className="font-medium">
          {shard.duration_seconds ? `${shard.duration_seconds.toFixed(1)}s` : "n/a"}
        </div>
        <div className="text-muted-foreground">
          {shard.rate_limited ? "rate limited" : shard.error ? "error" : "ok"}
        </div>
      </td>
    </tr>
  );
}

export default function OrchestratorPage() {
  const [data, setData] = useState<OrchestratorCurrentResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getOrchestratorCurrent();
      setData(resp);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, []);

  const stats = useMemo(() => {
    const shards = data?.shards ?? [];
    return {
      total: shards.length,
      running: shards.filter((s) => s.status === "running").length,
      retrying: shards.filter((s) => s.status === "retrying").length,
      failed: shards.filter((s) => s.status === "failed").length,
      completed: shards.filter((s) => s.status === "completed").length,
      pending: shards.filter((s) => s.status === "pending").length,
    };
  }, [data]);

  if (!data) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  const stages = Object.values(data.stages ?? {});
  const recentEvents = (data.recent_events ?? []) as Array<Record<string, any>>;
  const quotaEntries = Object.entries((data.quota as Record<string, any>) ?? {}) as Array<[string, any]>;

  const completionVariant = shardStatusVariant(data.completion_status);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-muted-foreground" />
            <h1 className="text-base font-semibold">Orchestrator Monitor</h1>
            {loading && (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            )}
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {data.task || "No active task"} · {data.cwd || "cwd unavailable"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={completionVariant}>{data.completion_status}</Badge>
          <Button variant="outline" size="sm" onClick={refresh} className="h-8 text-xs">
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 border border-destructive/30 bg-destructive/[0.06] p-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Status"
          value={data.status}
          hint={`updated ${data.updated_at ? isoTimeAgo(data.updated_at) : "just now"}`}
        />
        <MetricCard label="Completion" value={data.completion_status} hint={`run ${data.run_id || "n/a"}`} />
        <MetricCard
          label="Shards"
          value={`${stats.completed}/${stats.total}`}
          hint={`${stats.running} running · ${stats.retrying} retrying`}
        />
        <MetricCard
          label="Failures"
          value={`${stats.failed}`}
          hint={`${stats.pending} pending · ${stats.completed} completed`}
        />
      </div>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="text-base">Stage Overview</CardTitle>
            <Badge variant="secondary" className="text-[10px]">
              <ListChecks className="mr-1 h-3.5 w-3.5" />
              live
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {stages.length === 0 ? (
            <div className="text-sm text-muted-foreground">No stage data yet.</div>
          ) : (
            stages.map((stage) => <StageCard key={stage.role} stage={stage} />)
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="text-base">Shard Board</CardTitle>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline" className="text-[10px]">completed {stats.completed}</Badge>
              <Badge variant="outline" className="text-[10px]">running {stats.running}</Badge>
              <Badge variant="outline" className="text-[10px]">retrying {stats.retrying}</Badge>
              <Badge variant="outline" className="text-[10px]">failed {stats.failed}</Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="max-h-[680px] overflow-auto">
            <table className="w-full text-left">
              <thead className="sticky top-0 border-b border-border/60 bg-background/95 backdrop-blur">
                <tr className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                  <th className="px-3 py-2">Role</th>
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">Task</th>
                  <th className="px-3 py-2">Provider</th>
                  <th className="px-3 py-2 text-right">Duration</th>
                </tr>
              </thead>
              <tbody>
                {data.shards.length === 0 ? (
                  <tr>
                    <td className="px-3 py-6 text-sm text-muted-foreground" colSpan={5}>
                      No shard data available yet.
                    </td>
                  </tr>
                ) : (
                  data.shards.map((shard) => <ShardRow key={`${shard.role}:${shard.shard_index}`} shard={shard} />)
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Recent Events</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="max-h-[360px] overflow-auto font-mono-ui text-xs leading-5">
            {recentEvents.slice(-60).map((event, index) => (
              <div key={index} className="border-b border-border/40 py-2 last:border-b-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary" className="text-[10px] uppercase">
                    {String(event.kind ?? "event")}
                  </Badge>
                  {event.role && <span>{String(event.role)}</span>}
                  {event.shard_index != null && <span>shard {String(event.shard_index)}</span>}
                  {event.provider_id && <span>{String(event.provider_id)}</span>}
                  {event.success != null && (
                    <span className={event.success ? "text-success" : "text-destructive"}>
                      {event.success ? "success" : "failure"}
                    </span>
                  )}
                  {event.ts && <span className="text-muted-foreground">{isoTimeAgo(new Date(Number(event.ts) * 1000).toISOString())}</span>}
                </div>
                {event.shard_task && (
                  <div className="mt-1 text-muted-foreground">{preview(String(event.shard_task), 240)}</div>
                )}
                {event.error && <div className="mt-1 text-destructive">{preview(String(event.error), 240)}</div>}
              </div>
            ))}
            {(data.recent_events ?? []).length === 0 && (
              <div className="text-muted-foreground">No recent events captured yet.</div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Quota Snapshot</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-2">
          {quotaEntries.map(([family, info]) => (
            <div key={family} className="rounded border border-border/60 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="font-medium capitalize">{family}</div>
                <Badge variant="outline" className="text-[10px]">
                  {info.window_key ?? "n/a"}
                </Badge>
              </div>
              <div className="mt-3 grid gap-2 text-xs text-muted-foreground">
                <div>Last reset: {info.last_reset_at ?? "n/a"}</div>
                <div>Next reset: {info.next_reset_at ?? "n/a"}</div>
                <div>Requests: {info.totals?.requests ?? 0}</div>
                <div>Tokens: {formatTokenCount(Number(info.totals?.tokens ?? 0))}</div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
