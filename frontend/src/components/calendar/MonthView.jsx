import { useMemo } from "react";
import { format, isSameDay, isSameMonth, parseISO } from "date-fns";
import { cn } from "@/lib/utils";
import { categoryStyle, eventStart, isMultiDay, weekDays } from "./calendarUtils";

const MAX_VISIBLE = 3;

function EventChip({ ev, onSelect }) {
  const style = categoryStyle(ev.category);
  const cancelled = ev.status === "cancelled";
  const multi = isMultiDay(ev);
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onSelect(ev); }}
      title={ev.title}
      data-testid="calendar-event-chip"
      className={cn(
        "w-full flex items-center gap-1.5 rounded px-1.5 h-[22px] text-left text-[11.5px] leading-none transition-colors",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        multi ? style.solid : cn(style.chip, "hover:brightness-95 dark:hover:brightness-125"),
        cancelled && "opacity-60 line-through",
      )}
    >
      {!multi && <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", style.dot)} />}
      {!multi && <span className="shrink-0 tabular-nums opacity-80">{format(eventStart(ev), "h:mm")}</span>}
      <span className="truncate font-medium">{ev.title}</span>
    </button>
  );
}

export default function MonthView({ data, anchorDate, onSelectEvent, onSelectDay, onCreateAt }) {
  const byId = useMemo(() => new Map(data.events.map((e) => [e.id, e])), [data.events]);
  const today = new Date();
  const headers = weekDays(anchorDate);

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden" data-testid="calendar-month-view">
      <div className="grid grid-cols-7 border-b border-border bg-muted/40">
        {headers.map((d) => (
          <div key={d.toISOString()} className="px-2 py-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            <span className="hidden sm:inline">{format(d, "EEE")}</span>
            <span className="sm:hidden">{format(d, "EEEEE")}</span>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 auto-rows-fr">
        {data.days.map(({ date, event_ids }, i) => {
          const day = parseISO(date);
          const events = event_ids.map((id) => byId.get(id)).filter(Boolean)
            .sort((a, b) => Number(isMultiDay(b)) - Number(isMultiDay(a)));
          const outside = !isSameMonth(day, anchorDate);
          const isToday = isSameDay(day, today);
          const hidden = events.length - MAX_VISIBLE;
          return (
            <div
              key={date}
              role="button"
              tabIndex={0}
              aria-label={`${format(day, "d MMMM yyyy")}, ${events.length} events`}
              onClick={() => onCreateAt(day)}
              onKeyDown={(e) => { if (e.key === "Enter") onSelectDay(day); }}
              className={cn(
                "group min-h-[112px] p-1.5 flex flex-col gap-1 cursor-pointer transition-colors",
                "hover:bg-primary/[0.03] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                i % 7 !== 6 && "border-r border-border",
                i < data.days.length - 7 && "border-b border-border",
                outside && "bg-muted/30",
              )}
            >
              <div className="flex items-center justify-between px-0.5">
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onSelectDay(day); }}
                  className={cn(
                    "h-6 min-w-6 px-1.5 rounded-full text-[12px] font-medium tabular-nums transition-colors",
                    isToday ? "bg-primary text-primary-foreground" : "hover:bg-muted",
                    outside && !isToday && "text-muted-foreground/60",
                  )}
                >
                  {format(day, "d")}
                </button>
              </div>
              <div className="flex flex-col gap-0.5 min-w-0">
                {events.slice(0, MAX_VISIBLE).map((ev) => (
                  <EventChip key={ev.id} ev={ev} onSelect={onSelectEvent} />
                ))}
                {hidden > 0 && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onSelectDay(day); }}
                    className="text-left px-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground"
                  >
                    +{hidden} more
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
