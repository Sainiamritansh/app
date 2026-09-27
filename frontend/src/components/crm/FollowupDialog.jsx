import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/contexts/AuthContext";
import { can } from "@/constants/permissions";
import { api, formatApiError } from "@/lib/api";
import { todayLocal } from "./crmShared";

/** Active users who may own a record in `module` (by the module's view action). */
export function useOwnerOptions(action, open = true) {
  const [people, setPeople] = useState([]);
  useEffect(() => {
    if (!open) return;
    api.get("/users/directory").then(({ data }) => setPeople(data)).catch(() => setPeople([]));
  }, [open]);
  return useMemo(() => people.filter((p) => can(p.role, action)), [people, action]);
}

/** Create (customerId) or edit (followup) a CRM follow-up. */
export default function FollowupDialog({ open, onOpenChange, customerId, customerName, followup, onSaved }) {
  const { user } = useAuth();
  const owners = useOwnerOptions("crm.view", open);
  const [form, setForm] = useState({ title: "", due_date: todayLocal(1), owner_id: "", notes: "" });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setForm(followup
      ? { title: followup.title, due_date: followup.due_date, owner_id: followup.owner_id, notes: followup.notes || "" }
      : { title: "", due_date: todayLocal(1), owner_id: user?.id || "", notes: "" });
  }, [open, followup, user]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e?.target ? e.target.value : e }));

  async function submit(e) {
    e.preventDefault();
    if (!form.title.trim()) return toast.error("Give the follow-up a title");
    if (!form.due_date) return toast.error("Pick a due date");
    setSaving(true);
    try {
      const body = { title: form.title.trim(), due_date: form.due_date, owner_id: form.owner_id || null, notes: form.notes.trim() || null };
      const { data } = followup
        ? await api.patch(`/crm/followups/${followup.id}`, body)
        : await api.post(`/crm/customers/${customerId}/followups`, body);
      toast.success(followup ? "Follow-up updated" : "Follow-up scheduled");
      onSaved?.(data);
      onOpenChange(false);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle className="font-display">{followup ? "Edit follow-up" : "Schedule follow-up"}</DialogTitle>
            <DialogDescription>
              {customerName ? `With ${customerName}. ` : ""}The owner is notified when someone else assigns it.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="fu-title">What needs to happen</Label>
              <Input id="fu-title" value={form.title} onChange={set("title")} maxLength={200} placeholder="e.g. Call about monthly plan renewal" data-testid="crm-followup-title" />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="fu-due">Due date</Label>
                <Input id="fu-due" type="date" value={form.due_date} onChange={set("due_date")} data-testid="crm-followup-due" />
              </div>
              <div className="space-y-1.5">
                <Label>Owner</Label>
                <Select value={form.owner_id || undefined} onValueChange={set("owner_id")}>
                  <SelectTrigger data-testid="crm-followup-owner"><SelectValue placeholder="Me" /></SelectTrigger>
                  <SelectContent>
                    {owners.map((o) => <SelectItem key={o.id} value={o.id}>{o.name}{o.id === user?.id ? " (me)" : ""} · {o.role}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="fu-notes">Notes (optional)</Label>
              <Textarea id="fu-notes" value={form.notes} onChange={set("notes")} rows={3} maxLength={2000} />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={saving} data-testid="crm-followup-save">
              {saving && <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />}{followup ? "Save" : "Schedule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
