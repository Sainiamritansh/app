import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts";
import { IndianRupee, Percent, Receipt, Wallet, FileWarning, Landmark, RefreshCw, BarChart3, Building2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatCard, EmptyState } from "@/components/module/ModulePrimitives";
import { cn } from "@/lib/utils";
import {
  inr, inrCompact, useFinanceResource, LoadError, SkeletonBlocks, ChartTooltip, monthLabel,
} from "@/components/finance/financeShared";

function vsLast(cur, prev) {
  if (!prev) return cur ? "New this month" : "No revenue yet";
  const d = ((cur - prev) / prev) * 100;
  return `${d >= 0 ? "+" : ""}${d.toFixed(1)}% vs last month`;
}

export default function FinanceOverview({ onNavigate }) {
  const { data, error, loading, reload } = useFinanceResource("/finance/overview", null, { interval: 60000 });

  if (!data && error) return <LoadError error={error} onRetry={reload} loading={loading} what="the finance overview" />;
  if (!data) {
    return (
      <div className="space-y-4">
        <SkeletonBlocks count={6} />
        <div className="h-[320px] rounded-xl bg-muted animate-pulse" />
      </div>
    );
  }

  const c = data.cards;
  const hasSeries = data.series.some((s) => s.revenue > 0);

  return (
    <div className="space-y-6" data-testid="finance-overview">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-muted-foreground">
        <span>
          {monthLabel(data.month)} to date · platform commission <span className="font-medium text-foreground">{data.commission_pct}%</span>
          {" "}· GST <span className="font-medium text-foreground">{data.gst_pct}%</span>
          {" "}<button type="button" className="text-primary hover:underline" onClick={() => onNavigate("settings")}>Edit</button>
          {error && <span className="text-destructive"> · Refresh failed: {error}</span>}
        </span>
        <Button variant="ghost" size="sm" onClick={reload} disabled={loading} className="h-8" aria-label="Refresh overview" data-testid="finance-overview-refresh">
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </Button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        <StatCard label="Gross booking revenue (MTD)" value={inr(c.gross_mtd)} icon={IndianRupee}
                  sub={vsLast(c.gross_mtd, c.gross_prev)} />
        <StatCard label="Platform commission (MTD)" value={inr(c.commission_mtd)} icon={Percent} tone="success"
                  sub={`${c.bookings_mtd} revenue booking${c.bookings_mtd === 1 ? "" : "s"}`} />
        <StatCard label="GST collected (MTD)" value={inr(c.gst_mtd)} icon={Landmark} tone="info" sub="Issued + paid invoices" />
        <StatCard label="Receivables" value={inr(c.receivable)} icon={Receipt} tone="warning"
                  sub={`${c.receivable_count} unpaid invoice${c.receivable_count === 1 ? "" : "s"}`} />
        <StatCard label="Vendor payouts pending" value={inr(c.payouts_pending)} icon={Wallet} tone="warning"
                  sub={`${c.payouts_pending_count} awaiting transfer`} />
        <StatCard label="Bookings to invoice" value={c.uninvoiced_bookings.toLocaleString("en-IN")} icon={FileWarning}
                  tone={c.uninvoiced_bookings ? "danger" : "default"}
                  sub={c.draft_invoices ? `${c.draft_invoices} draft invoice${c.draft_invoices === 1 ? "" : "s"}` : "Confirmed · active · completed"} />
        {c.bills_pending_count > 0 && (
          <button type="button" className="text-left" onClick={() => onNavigate("bills")} data-testid="finance-overview-bills">
            <StatCard label="Vendor bills payable" value={inr(c.bills_pending)} icon={Building2}
                      tone={c.bills_overdue ? "danger" : "warning"}
                      sub={c.bills_overdue ? `${inr(c.bills_overdue)} overdue` : `${c.bills_pending_count} pending bill${c.bills_pending_count === 1 ? "" : "s"}`} />
          </button>
        )}
      </div>

      <Card className="border-border" data-testid="finance-revenue-chart">
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-2">
            <div>
              <CardTitle className="font-display text-[17px]">Revenue & commission</CardTitle>
              <CardDescription>Gross booking revenue vs WavyGo's commission · last 12 months (IST)</CardDescription>
            </div>
            <Badge variant="secondary" className="text-[10px]">12M</Badge>
          </div>
        </CardHeader>
        <CardContent className="pt-2">
          {!hasSeries ? (
            <EmptyState icon={BarChart3} title="No revenue in the last 12 months"
                        description="Confirmed, active and completed marketplace bookings will appear here." />
          ) : (
            <div className="h-[280px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data.series} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="finRevFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(var(--chart-1))" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="hsl(var(--chart-1))" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="finComFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(var(--chart-2))" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="hsl(var(--chart-2))" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tickFormatter={inrCompact} width={56} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip content={<ChartTooltip />} cursor={{ stroke: "hsl(var(--border))" }} />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                  <Area type="monotone" dataKey="revenue" name="Gross revenue" stroke="hsl(var(--chart-1))" fill="url(#finRevFill)" strokeWidth={2.5} />
                  <Area type="monotone" dataKey="commission" name="Commission" stroke="hsl(var(--chart-2))" fill="url(#finComFill)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
