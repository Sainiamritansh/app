import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useLiveRefresh } from "@/hooks/useLiveRefresh";
import { api } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { AlertTriangle, ScrollText } from "lucide-react";

const ALL = "__all__";

export default function ActivityLogs() {
  const [logs, setLogs] = useState([]);
  const [q, setQ] = useState("");
  const [module, setModule] = useState(ALL);
  const [modules, setModules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Module filtering happens server-side (?module=) so the 200-row limit applies per module.
  const load = useCallback(({ background = false } = {}) => {
    if (!background) {
      setLoading(true);
      setError(null);
    }
    const params = { limit: 200, ...(module !== ALL ? { module } : {}) };
    api.get("/activity", { params })
      .then(({ data }) => setLogs(data))
      .catch((e) => { if (!background) setError(e?.response?.data?.detail || "Could not load activity logs."); })
      .finally(() => { if (!background) setLoading(false); });
  }, [module]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load, 30000);
  useEffect(() => { api.get("/activity/modules").then(({ data }) => setModules(data)).catch(() => setModules([])); }, []);

  const filtered = logs.filter((l) => {
    if (!q) return true;
    const t = q.toLowerCase();
    return [l.user_name, l.action, l.module, l.target].filter(Boolean).some((s) => s.toLowerCase().includes(t));
  });

  return (
    <div className="space-y-6">
      <div>
        <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Audit</div>
        <h1 className="font-display text-3xl font-semibold tracking-tight mt-1">Activity Logs</h1>
        <p className="text-sm text-muted-foreground mt-2 max-w-xl">Every meaningful action across WavyGo OS lands here. Future modules append to the same audit trail.</p>
      </div>

      <div className="flex flex-col sm:flex-row gap-3">
        <Input placeholder="Filter by user, action, module…" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-md" data-testid="activity-search" />
        <Select value={module} onValueChange={setModule}>
          <SelectTrigger className="w-full sm:w-[200px]" data-testid="activity-module-filter"><SelectValue placeholder="All modules" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All modules</SelectItem>
            {modules.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      <Card className="border-border">
        {error ? (
          <div className="p-16 text-center" data-testid="activity-error">
            <AlertTriangle className="h-8 w-8 mx-auto text-destructive" />
            <div className="mt-3 text-sm text-muted-foreground">{String(error)}</div>
            <Button variant="outline" size="sm" onClick={load} className="mt-4 h-8 text-xs">Retry</Button>
          </div>
        ) : loading && logs.length === 0 ? (
          <div className="p-16 text-center text-sm text-muted-foreground">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="p-16 text-center">
            <ScrollText className="h-8 w-8 mx-auto text-muted-foreground" />
            <div className="mt-3 text-sm text-muted-foreground">No activity matches your filter.</div>
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {filtered.map((l) => (
              <li key={l.id} className="px-6 py-3.5 flex items-center gap-3">
                <div className="h-8 w-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-[11px] font-semibold">
                  {(l.user_name || "?").split(" ").map(s => s[0]).slice(0, 2).join("")}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13.5px]">
                    <span className="font-medium text-foreground">{l.user_name}</span>{" "}
                    <span className="text-muted-foreground">{(l.action || "").toLowerCase()}</span>
                    {l.target && <span className="text-foreground"> · {l.target}</span>}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {(() => { try { return formatDistanceToNow(new Date(l.created_at), { addSuffix: true }); } catch { return ""; } })()}
                  </div>
                </div>
                {l.module && <Badge variant="secondary" className="text-[10px]">{l.module}</Badge>}
                <Badge className="bg-wavygo-50 text-wavygo-800 hover:bg-wavygo-50 text-[10px]">{l.user_role}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
