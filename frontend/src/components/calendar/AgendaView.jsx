import { useMemo } from "react";
import { format, isBefore, startOfDay } from "date-fns";
import { MapPin, Users, Video, Loader2, CalendarDays } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/module/ModulePrimitives";
import { cn } from "@/lib/utils";
import { categoryStyle, dayLabel, eventEnd, eventStart, formatEventTime, isInProgress } from "./calendarUtils";

/** Group events under the local day they start on (or the agenda start, for ones already running). */
function groupByDay(events, from) {
  const groups = new Map();
  for (const ev of events) {
    let day = startOfDay(eventStart(ev));
    if (isBefore(day, from)) day = from;
    const key = day.toISOString();
    if (!groups.has(key)) groups.set(key, { day, events: [] });
    groups.get(key).events.push(ev);
  }
  return [...groups.values()];
}

export default function AgendaView({ data, from, onSelectEvent, onLoadMore, loadingMore, onCreate }) {
  const groups = useMemo(() => groupByDay(data.items, startOfDay(from)), [data.items, from]);
  const now = new Date();

  if (!data.items.length) {
    return (
      <EmptyState
        icon={CalendarDays}
        title="Nothing scheduled"
        description="No events in this period. Try a different date range or clear your filters."
        action={onCreate && <Button size="sm" onClick={onCreate}>Schedule an event</Button>}
      />
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card divide-y divide-border" data-testid="calendar-agenda-view">
      {groups.map(({ day, events }) => (
        <section key={day.toISOString()} className="flex flex-col sm:flex-row">
          <div className="sm:w-40 shrink-0 px-4 pt-4 sm:pb-4">
            <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{dayLabel(day)}</div>
            <div className="font-display text-[15px] font-semibold text-foreground mt-0.5">{format(day, "d MMMM yyyy")}</div>
          </div>
          <ul className="flex-1 p-2 sm:py-3">
            {events.map((ev) => {
              const style = categoryStyle(ev.category);
              const live = isInProgress(ev, now);
              // Earlier today's meetings stay listed, just de-emphasised once they are over.
              const past = !ev.all_day && eventEnd(ev) <= now;
              return (
                <li key={ev.id}>
                  <button
                    type="button"
                    onClick={() => onSelectEvent(ev)}
                    data-testid="calendar-agenda-item"
                    className={cn(
                      "w-full flex items-start gap-3 rounded-lg px-2.5 py-2.5 text-left hover:bg-muted/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      past && "opacity-60",
                    )}
                  >
                    <span className={cn("mt-1 h-9 w-1 rounded-full shrink-0", style.dot)} />
                    <div className="w-32 shrink-0 text-[12.5px] text-muted-foreground tabular-nums pt-0.5">{formatEventTime(ev)}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={cn("text-[14px] font-medium text-foreground", ev.status === "cancelled" && "line-through text-muted-foreground")}>
                          {ev.title}
                        </span>
                        {live && ev.status !== "cancelled" && (
                          <span className="inline-flex items-center h-5 px-1.5 rounded text-[10.5px] font-semibold uppercase tracking-wide bg-destructive/10 text-destructive">Now</span>
                        )}
                        {ev.status !== "confirmed" && (
                          <span className="inline-flex items-center h-5 px-1.5 rounded text-[10.5px] font-semibold uppercase tracking-wide bg-muted text-foreground/70">{ev.status}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 flex-wrap text-[12px] text-muted-foreground mt-1">
                        <span>{ev.category}</span>
                        {ev.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{ev.location}</span>}
                        {ev.meeting_link && <span className="inline-flex items-center gap-1"><Video className="h-3 w-3" />Online</span>}
                        {ev.participants?.length > 0 && (
                          <span className="inline-flex items-center gap-1"><Users className="h-3 w-3" />{ev.participants.length}</span>
                        )}
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {data.has_more && (
        <div className="p-3 flex justify-center">
          <Button variant="outline" size="sm" onClick={onLoadMore} disabled={loadingMore} className="gap-1.5">
            {loadingMore && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Load more ({data.total - data.items.length} remaining)
          </Button>
        </div>
      )}
    </div>
  );
}
