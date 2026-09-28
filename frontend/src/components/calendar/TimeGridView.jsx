import { useEffect, useMemo, useRef, useState } from "react";
import { addDays, format, isSameDay, startOfDay } from "date-fns";
import { cn } from "@/lib/utils";
import {
  categoryStyle, eventEnd, eventStart, formatEventTime, lastEventDay, minutesIntoDay,
} from "./calendarUtils";

const HOUR_HEIGHT = 48;
const MIN_EVENT_HEIGHT = 22;
const HOURS = Array.from({ length: 24 }, (_, h) => h);
const SCROLL_TO_HOUR = 8;
// Overlapping events cascade: each is offset by its column but drawn wider than a strict
// 1/n slice, so titles stay readable in a narrow week column.
const CASCADE_WIDTH = 1.7;

const DAY_MS = 24 * 3600 * 1000;

/** All-day events and anything lasting a full day or more go in the top row; the rest in the grid. */
const inAllDayRow = (ev) => ev.all_day || eventEnd(ev) - eventStart(ev) >= DAY_MS;

/** Clip timed events to one day and lay out overlapping ones side by side. */
function layoutDay(day, events) {
  const dayStart = startOfDay(day);
  const dayEnd = addDays(dayStart, 1);
  const segments = [];
  for (const ev of events) {
    if (inAllDayRow(ev)) continue;
    const s = eventStart(ev);
    const e = eventEnd(ev);
    const touches = +s === +e ? s >= dayStart && s < dayEnd : s < dayEnd && e > dayStart;
    if (!touches) continue;
    segments.push({ ev, start: s < dayStart ? dayStart : s, end: e > dayEnd ? dayEnd : e });
  }
  segments.sort((a, b) => a.start - b.start || b.end - a.end);

  // Greedy columns within each cluster of visually overlapping segments.
  const minMs = (MIN_EVENT_HEIGHT / HOUR_HEIGHT) * 3600 * 1000;
  const placed = [];
  let cluster = [];
  let clusterEnd = 0;
  const flush = () => {
    const cols = Math.max(...cluster.map((c) => c.col)) + 1;
    cluster.forEach((c) => placed.push({ ...c, cols }));
    cluster = [];
  };
  for (const seg of segments) {
    if (cluster.length && +seg.start >= clusterEnd) flush();
    const visualEnd = Math.max(+seg.end, +seg.start + minMs);
    const taken = new Set(cluster.filter((c) => c.visualEnd > +seg.start).map((c) => c.col));
    let col = 0;
    while (taken.has(col)) col += 1;
    cluster.push({ ...seg, col, visualEnd });
    clusterEnd = cluster.length === 1 ? visualEnd : Math.max(clusterEnd, visualEnd);
  }
  if (cluster.length) flush();
  return placed;
}

function useNow() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60 * 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function AllDayRow({ days, events, onSelectEvent }) {
  const long = events.filter(inAllDayRow);
  if (!long.length) return null;
  return (
    <div className="flex border-b border-border">
      <div className="w-14 shrink-0 px-2 py-1.5 text-[10px] uppercase tracking-wide text-muted-foreground text-right">All day</div>
      <div className="flex-1 grid" style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }}>
        {days.map((day) => {
          const todays = long.filter((ev) => startOfDay(eventStart(ev)) <= day && day <= lastEventDay(ev));
          return (
            <div key={day.toISOString()} className="border-l border-border p-1 space-y-0.5 min-w-0">
              {todays.map((ev) => (
                <button
                  key={ev.id}
                  type="button"
                  onClick={() => onSelectEvent(ev)}
                  title={ev.title}
                  className={cn(
                    "w-full truncate rounded px-1.5 h-[22px] text-left text-[11.5px] font-medium",
                    categoryStyle(ev.category).solid,
                    ev.status === "cancelled" && "opacity-60 line-through",
                  )}
                >
                  {ev.title}
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function TimeGridView({ days, events, onSelectEvent, onCreateAt, onSelectDay }) {
  const scrollRef = useRef(null);
  const now = useNow();
  const layouts = useMemo(() => days.map((d) => layoutDay(d, events)), [days, events]);
  const cols = { gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` };

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = SCROLL_TO_HOUR * HOUR_HEIGHT;
  }, []);

  const handleSlotClick = (day, e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const minutes = Math.floor(((e.clientY - rect.top) / HOUR_HEIGHT) * 60 / 30) * 30;
    const at = new Date(day);
    at.setHours(0, Math.max(0, Math.min(minutes, 23 * 60 + 30)), 0, 0);
    onCreateAt(at);
  };

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden" data-testid={`calendar-${days.length === 1 ? "day" : "week"}-view`}>
      <div className="flex border-b border-border bg-muted/40">
        <div className="w-14 shrink-0" />
        <div className="flex-1 grid" style={cols}>
          {days.map((day) => {
            const isToday = isSameDay(day, now);
            return (
              <button
                key={day.toISOString()}
                type="button"
                onClick={() => onSelectDay(day)}
                disabled={days.length === 1}
                className="border-l border-border py-2 flex flex-col items-center gap-0.5 disabled:cursor-default enabled:hover:bg-muted/60"
              >
                <span className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{format(day, "EEE")}</span>
                <span className={cn(
                  "h-7 min-w-7 px-1.5 rounded-full flex items-center justify-center font-display text-[15px] font-semibold tabular-nums",
                  isToday && "bg-primary text-primary-foreground",
                )}>
                  {format(day, "d")}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <AllDayRow days={days} events={events} onSelectEvent={onSelectEvent} />

      <div ref={scrollRef} className="relative overflow-y-auto scrollbar-thin" style={{ maxHeight: "min(68vh, 720px)" }}>
        <div className="flex" style={{ height: 24 * HOUR_HEIGHT }}>
          <div className="w-14 shrink-0 relative">
            {HOURS.slice(1).map((h) => (
              <div key={h} className="absolute right-2 -translate-y-1/2 text-[10.5px] text-muted-foreground tabular-nums" style={{ top: h * HOUR_HEIGHT }}>
                {format(new Date(2000, 0, 1, h), "h a")}
              </div>
            ))}
          </div>
          <div className="flex-1 grid relative" style={cols}>
            {days.map((day, i) => (
              <div
                key={day.toISOString()}
                className="relative border-l border-border cursor-pointer"
                onClick={(e) => handleSlotClick(day, e)}
              >
                {HOURS.map((h) => (
                  <div key={h} className="absolute inset-x-0 border-t border-border/70" style={{ top: h * HOUR_HEIGHT }}>
                    <div className="border-t border-dashed border-border/40" style={{ marginTop: HOUR_HEIGHT / 2 - 1 }} />
                  </div>
                ))}

                {layouts[i].map(({ ev, start, end, col, cols: n }) => {
                  const style = categoryStyle(ev.category);
                  const top = (minutesIntoDay(start) / 60) * HOUR_HEIGHT;
                  const minutes = (end - start) / 60000;
                  const height = Math.max((minutes / 60) * HOUR_HEIGHT, MIN_EVENT_HEIGHT);
                  const compact = height < 44;
                  return (
                    <button
                      key={ev.id}
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onSelectEvent(ev); }}
                      title={`${ev.title} · ${formatEventTime(ev)}`}
                      data-testid="calendar-event-block"
                      className={cn(
                        "absolute rounded-md border-l-[3px] px-1.5 py-1 text-left overflow-hidden shadow-sm",
                        "bg-card ring-1 ring-card hover:ring-primary/40 hover:z-20 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        style.bar,
                        ev.status === "cancelled" && "opacity-60",
                      )}
                      style={{
                        top: top + 1,
                        height: height - 2,
                        left: `calc(${(col / n) * 100}% + 2px)`,
                        width: `calc(${Math.min(CASCADE_WIDTH / n, (n - col) / n) * 100}% - 4px)`,
                        zIndex: 10 + col,
                      }}
                    >
                      <div className={cn("absolute inset-0 -z-10", style.chip)} />
                      <div className={cn("text-[11.5px] font-medium leading-tight truncate", ev.status === "cancelled" && "line-through")}>
                        {ev.title}
                      </div>
                      {!compact && (
                        <div className="text-[10.5px] text-muted-foreground mt-0.5 truncate">
                          {formatEventTime(ev)}{ev.location ? ` · ${ev.location}` : ""}
                        </div>
                      )}
                    </button>
                  );
                })}

                {isSameDay(day, now) && (
                  <div className="absolute inset-x-0 z-30 pointer-events-none" style={{ top: (minutesIntoDay(now) / 60) * HOUR_HEIGHT }}>
                    <div className="relative border-t-2 border-destructive">
                      <span className="absolute -left-1 -top-[5px] h-2 w-2 rounded-full bg-destructive" />
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
