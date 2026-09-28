import { useState } from "react";
import { CalendarClock } from "lucide-react";
import { EmptyState } from "@/components/module/ModulePrimitives";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useResource } from "./crmShared";
import { ErrorBlock, RowSkeletons } from "./StateBlocks";
import { FollowupItem, useFollowupActions } from "./Customer360Sheet";
import FollowupDialog from "./FollowupDialog";

export default function FollowupsPanel({ canEdit, onOpenCustomer, refreshKey }) {
  const [status, setStatus] = useState("open");
  const [mine, setMine] = useState(false);
  const [editing, setEditing] = useState(null);
  const { data, error, loading, reload } = useResource("/crm/followups", { status, mine: mine || undefined, _k: refreshKey });
  const actions = useFollowupActions(() => reload({ background: true }));

  const overdue = data ? data.filter((f) => f.overdue).length : 0;

  return (
    <div className="space-y-4" data-testid="crm-followups">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <Tabs value={status} onValueChange={setStatus}>
          <TabsList className="bg-muted/60 p-1">
            <TabsTrigger value="open">Open</TabsTrigger>
            <TabsTrigger value="done">Done</TabsTrigger>
            <TabsTrigger value="all">All</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex items-center gap-2">
          <Label htmlFor="fu-mine" className="text-[13px] cursor-pointer">Only mine</Label>
          <Switch id="fu-mine" checked={mine} onCheckedChange={setMine} data-testid="crm-followups-mine" />
        </div>
      </div>
      {status === "open" && overdue > 0 && (
        <div className="text-[12.5px] text-destructive font-medium">{overdue} overdue follow-up{overdue === 1 ? "" : "s"}</div>
      )}
      {error && !data ? (
        <ErrorBlock title="Couldn't load follow-ups" error={error} onRetry={reload} />
      ) : !data || (loading && !data) ? (
        <RowSkeletons rows={4} />
      ) : data.length === 0 ? (
        <EmptyState icon={CalendarClock} title={status === "done" ? "Nothing completed yet" : "No open follow-ups"}
                    description="Schedule follow-ups from a customer's profile; owners are notified." />
      ) : (
        <ul className="space-y-2">
          {data.map((f) => (
            <FollowupItem key={f.id} f={f} canEdit={canEdit} busy={actions.busy} showCustomer onOpenCustomer={onOpenCustomer}
                          onToggle={actions.toggle} onDelete={actions.remove} onEdit={setEditing} />
          ))}
        </ul>
      )}
      <FollowupDialog open={Boolean(editing)} onOpenChange={(o) => !o && setEditing(null)} followup={editing}
                      customerName={editing?.customer_name} onSaved={() => reload({ background: true })} />
    </div>
  );
}
