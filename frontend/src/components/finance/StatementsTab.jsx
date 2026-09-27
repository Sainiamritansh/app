import { useState } from "react";
import { Download, Loader2, ScrollText } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState } from "@/components/module/ModulePrimitives";
import { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import {
  inr, currentMonthIST, useFinanceResource, LoadError, SkeletonBlocks, MonthSelect, downloadCsv, TH,
} from "@/components/finance/financeShared";

const n = (v) => Number(v || 0).toLocaleString("en-IN");

function Line({ label, value, hint, strong, negative }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2 border-b border-border last:border-0">
      <div className="min-w-0">
        <div className={strong ? "text-[13.5px] font-semibold text-foreground" : "text-[13px] text-foreground"}>{label}</div>
        {hint && <div className="text-[11.5px] text-muted-foreground">{hint}</div>}
      </div>
      <div className={`whitespace-nowrap tabular-nums ${strong ? "font-display text-[15px] font-semibold" : "text-[13.5px]"} ${negative ? "text-muted-foreground" : "text-foreground"}`}>
        {value}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <Card className="border-border">
      <CardHeader className="pb-1"><CardTitle className="font-display text-[15px]">{title}</CardTitle></CardHeader>
      <CardContent className="pt-0">{children}</CardContent>
    </Card>
  );
}

export default function StatementsTab() {
  const [month, setMonth] = useState(currentMonthIST());
  const { data, error, loading, reload } = useFinanceResource("/finance/statements", { month });
  const [exporting, setExporting] = useState(false);

  async function exportCsv() {
    setExporting(true);
    try { await downloadCsv("/finance/statements/export", { month }, `wavygo-statement-${month}.csv`); }
    catch (e) { toast.error(formatApiError(e)); } finally { setExporting(false); }
  }

  const s = data?.summary;
  const empty = data && data.trailing.every((t) => !t.revenue_bookings && !t.invoices_issued && !t.cancelled_bookings && !t.pending_bookings);

  return (
    <div className="space-y-6" data-testid="finance-statements">
      <div className="flex flex-col sm:flex-row gap-2 sm:items-center justify-between">
        <div className="text-[13px] text-muted-foreground">
          Accrual view by booking month (IST). Commission uses the rate frozen in a payout, otherwise the current {data?.commission_pct ?? "—"}%.
        </div>
        <div className="flex gap-2">
          <MonthSelect value={month} onChange={(m) => setMonth(m || currentMonthIST())} testid="finance-statement-month" />
          <Button variant="outline" size="sm" className="h-9 shrink-0" onClick={exportCsv} disabled={exporting} data-testid="finance-statement-export">
            {exporting ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Download className="h-4 w-4 mr-1.5" />}CSV
          </Button>
        </div>
      </div>

      {!data && error ? (
        <LoadError error={error} onRetry={reload} loading={loading} what="the statement" />
      ) : !data ? (
        <SkeletonBlocks count={4} className="h-56" />
      ) : empty ? (
        <EmptyState icon={ScrollText} title="Nothing to report" description={`No bookings or invoices in the 12 months to ${data.label}.`} />
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            <Section title={`Revenue · ${data.label}`}>
              <Line label="Gross booking revenue" value={inr(s.gross_revenue)} hint={`${n(s.revenue_bookings)} confirmed / active / completed`} />
              <Line label="Vendor share" value={`− ${inr(s.vendor_earnings)}`} negative />
              <Line label="Platform commission" value={inr(s.platform_commission)} hint="WavyGo revenue" strong />
            </Section>
            <Section title="Vendor payouts">
              <Line label="Paid" value={inr(s.vendor_paid)} />
              <Line label="Owed" value={inr(s.vendor_owed)} hint="Not in a payout, or payout pending" strong />
            </Section>
            <Section title="Invoices & GST">
              <Line label="Issued" value={`${n(s.invoices_issued)} · ${inr(s.invoiced_amount)}`} />
              <Line label="Paid" value={`${n(s.invoices_paid)} · ${inr(s.paid_amount)}`} />
              <Line label="Outstanding" value={`${n(s.invoices_outstanding)} · ${inr(s.outstanding_amount)}`} />
              <Line label="GST collected" value={inr(s.gst_collected)} strong />
            </Section>
            <Section title="Cancellations & refunds">
              <Line label="Cancelled bookings" value={n(s.cancelled_bookings)} />
              <Line label="Pending bookings" value={n(s.pending_bookings)} hint="Not counted as revenue" />
              <Line label="Invoices voided" value={n(s.invoices_void)} />
              <Line label="Refunded" value={inr(s.refunded_amount)} hint="Paid invoices later voided" />
              <Line label="Revenue bookings not invoiced" value={n(s.uninvoiced_bookings)} />
            </Section>
          </div>

          <Card className="border-border">
            <CardHeader className="pb-2">
              <CardTitle className="font-display text-[17px]">Trailing 12 months</CardTitle>
              <CardDescription>{data.trailing[0].label} – {data.label}</CardDescription>
            </CardHeader>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className={TH}>Month</TableHead>
                  <TableHead className={`${TH} text-right`}>Gross</TableHead>
                  <TableHead className={`${TH} text-right`}>Commission</TableHead>
                  <TableHead className={`${TH} text-right`}>Vendor owed</TableHead>
                  <TableHead className={`${TH} text-right`}>Vendor paid</TableHead>
                  <TableHead className={`${TH} text-right`}>GST</TableHead>
                  <TableHead className={`${TH} text-right`}>Outstanding</TableHead>
                  <TableHead className={`${TH} text-right`}>Cancelled</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...data.trailing].reverse().map((t) => (
                  <TableRow key={t.month} className={t.month === month ? "bg-primary/5" : "hover:bg-muted/40"}>
                    <TableCell className="whitespace-nowrap font-medium">
                      <button type="button" className="hover:text-primary" onClick={() => setMonth(t.month)}>{t.label}</button>
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap">{inr(t.gross_revenue)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{inr(t.platform_commission)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{inr(t.vendor_owed)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{inr(t.vendor_paid)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{inr(t.gst_collected)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{inr(t.outstanding_amount)}</TableCell>
                    <TableCell className="text-right">{n(t.cancelled_bookings)}</TableCell>
                  </TableRow>
                ))}
                <TableRow className="bg-muted/30 font-medium">
                  <TableCell>Total</TableCell>
                  {["gross_revenue", "platform_commission", "vendor_owed", "vendor_paid", "gst_collected", "outstanding_amount"].map((k) => (
                    <TableCell key={k} className="text-right whitespace-nowrap">{inr(data.trailing_totals[k])}</TableCell>
                  ))}
                  <TableCell className="text-right">{n(data.trailing_totals.cancelled_bookings)}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}
