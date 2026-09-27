import { useState } from "react";
import { Clock, MapPin, Video, Users, Eye, Pencil, Trash2, Ban, RotateCcw, Loader2, ExternalLink, User, Bell } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/module/ModulePrimitives";
import { api, formatApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  VISIBILITY, canModifyEvent, categoryStyle, formatEventDate, formatEventTime, initials, reminderLabel, safeUrl,
} from "./calendarUtils";

function Row({ icon: Icon, children }) {
  return (
    <div className="flex items-start gap-3 text-[13.5px]">
      <Icon className="h-4 w-4 mt-0.5 text-muted-foreground shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export default function EventDetailsDialog({ event, open, onOpenChange, onEdit, onChanged, user, can }) {
  const [busy, setBusy] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (!event) return null;
  const style = categoryStyle(event.category);
  const canEdit = canModifyEvent(event, user, can, "calendar.edit_any");
  const canDelete = canModifyEvent(event, user, can, "calendar.delete_any");
  const link = safeUrl(event.meeting_link);
  const visibility = VISIBILITY.find((v) => v.key === event.visibility);
  const cancelled = event.status === "cancelled";

  const setStatus = async (status) => {
    setBusy(status);
    try {
      const { data } = await api.patch(`/calendar/events/${event.id}`, { status });
      toast.success(status === "cancelled" ? "Event cancelled. Participants were notified." : "Event restored");
      onChanged(data);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy("delete");
    try {
      await api.delete(`/calendar/events/${event.id}`);
      toast.success("Event deleted");
      setConfirmDelete(false);
      onChanged(null);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-lg" data-testid="calendar-event-details">
          <DialogHeader>
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              <span className={cn("h-2 w-2 rounded-full", style.dot)} />
              {event.category}
              {event.status !== "confirmed" && <StatusPill status={event.status} />}
            </div>
            <DialogTitle className={cn("font-display text-xl tracking-tight text-left", cancelled && "line-through text-muted-foreground")}>
              {event.title}
            </DialogTitle>
            <DialogDescription className="sr-only">Event details</DialogDescription>
          </DialogHeader>

          <div className="space-y-3.5">
            <Row icon={Clock}>
              <div className="text-foreground">{formatEventDate(event)}</div>
              <div className="text-muted-foreground text-[12.5px]">{formatEventTime(event)}</div>
            </Row>
            {event.location && <Row icon={MapPin}>{event.location}</Row>}
            {event.meeting_link && (
              <Row icon={Video}>
                {link ? (
                  <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline break-all">
                    Join meeting <ExternalLink className="h-3 w-3" />
                  </a>
                ) : (
                  <span className="break-all">{event.meeting_link}</span>
                )}
              </Row>
            )}
            <Row icon={User}>
              Organised by <span className="font-medium">{event.organizer_name || "Unknown"}</span>
            </Row>
            {event.participants?.length > 0 && (
              <Row icon={Users}>
                <div className="text-muted-foreground text-[12.5px] mb-2">{event.participants.length} participant{event.participants.length === 1 ? "" : "s"}</div>
                <div className="flex flex-wrap gap-1.5">
                  {event.participants.map((p) => (
                    <span key={p.id} className="inline-flex items-center gap-1.5 rounded-full border border-border pl-0.5 pr-2.5 py-0.5 text-[12px]">
                      <Avatar className="h-5 w-5">
                        <AvatarImage src={p.photo || undefined} alt="" />
                        <AvatarFallback className="text-[9px]">{initials(p.name)}</AvatarFallback>
                      </Avatar>
                      {p.name}
                    </span>
                  ))}
                </div>
              </Row>
            )}
            {reminderLabel(event.reminder_minutes) && (
              <Row icon={Bell}>Reminder {reminderLabel(event.reminder_minutes).toLowerCase()}</Row>
            )}
            {visibility && (
              <Row icon={Eye}>
                {visibility.label}
                {event.visibility === "department" && event.department ? ` · ${event.department}` : ""}
              </Row>
            )}
            {event.description && (
              <p className="text-[13.5px] leading-relaxed text-foreground/90 whitespace-pre-wrap border-t border-border pt-3.5">
                {event.description}
              </p>
            )}
          </div>

          {(canEdit || canDelete) && (
            <DialogFooter className="gap-2 sm:gap-2 sm:justify-between">
              <div className="flex gap-2">
                {canDelete && (
                  <Button variant="ghost" size="sm" className="gap-1.5 text-destructive hover:text-destructive hover:bg-destructive/10"
                          onClick={() => setConfirmDelete(true)} disabled={!!busy}>
                    <Trash2 className="h-4 w-4" /> Delete
                  </Button>
                )}
              </div>
              {canEdit && (
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" className="gap-1.5" disabled={!!busy}
                          onClick={() => setStatus(cancelled ? "confirmed" : "cancelled")}>
                    {busy === "cancelled" || busy === "confirmed"
                      ? <Loader2 className="h-4 w-4 animate-spin" />
                      : cancelled ? <RotateCcw className="h-4 w-4" /> : <Ban className="h-4 w-4" />}
                    {cancelled ? "Restore" : "Cancel event"}
                  </Button>
                  <Button size="sm" className="gap-1.5" onClick={() => onEdit(event)} disabled={!!busy} data-testid="calendar-edit-event">
                    <Pencil className="h-4 w-4" /> Edit
                  </Button>
                </div>
              )}
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{event.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes the event for everyone. To keep a record, cancel it instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy === "delete"}>Keep event</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => { e.preventDefault(); remove(); }}
              disabled={busy === "delete"}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {busy === "delete" && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
              Delete event
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
