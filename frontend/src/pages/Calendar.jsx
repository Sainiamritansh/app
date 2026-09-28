import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { isToday as isTodayDate, setHours, startOfDay } from "date-fns";
import { CalendarDays, ChevronLeft, ChevronRight, Loader2, Plus, RefreshCw, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/module/ModulePrimitives";
import { Button } from "@/components/ui/button";
import { Calendar as MiniCalendar } from "@/components/ui/calendar";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import MonthView from "@/components/calendar/MonthView";
import TimeGridView from "@/components/calendar/TimeGridView";
import AgendaView from "@/components/calendar/AgendaView";
import EventDialog from "@/components/calendar/EventDialog";
import EventDetailsDialog from "@/components/calendar/EventDetailsDialog";
import {
  AGENDA_DAYS, AGENDA_PAGE_SIZE, BROWSER_TZ, CATEGORIES, VIEWS, VIEW_KEYS, WEEK_STARTS_ON,
  categoryStyle, parseDateParam, rangeTitle, shiftDate, toDateParam, weekDays,
} from "@/components/calendar/calendarUtils";
import { useAuth } from "@/contexts/AuthContext";
import { useLiveRefresh } from "@/hooks/useLiveRefresh";
import { usePermission } from "@/hooks/usePermission";
import { api, formatApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

const DEFAULT_EVENT_HOUR = 10;

/** Today is the default, so it stays out of the URL. */
function setDateParam(params, date) {
  if (isTodayDate(date)) params.delete("date");
  else params.set("date", toDateParam(date));
}

function requestFor(view, date, filters, page = 1) {
  const params = {
    tz: BROWSER_TZ,
    category: filters.category || undefined,
    participant_id: filters.mine ? "me" : undefined,
    include_cancelled: filters.showCancelled || undefined,
  };
  if (view === "month") {
    return ["/calendar/month", { ...params, year: date.getFullYear(), month: date.getMonth() + 1, full_weeks: true, week_start: "monday" }];
  }
  if (view === "week") return ["/calendar/week", { ...params, date: toDateParam(date), week_start: "monday" }];
  if (view === "day") return ["/calendar/day", { ...params, date: toDateParam(date) }];
  // Agenda starts at local midnight of the chosen day, so meetings earlier today
  // (already finished or in progress) still appear under "Today".
  return ["/calendar/agenda", {
    ...params,
    start: toDateParam(date),
    days: AGENDA_DAYS, page, page_size: AGENDA_PAGE_SIZE,
  }];
}

function ViewSkeleton({ view }) {
  if (view === "agenda") {
    return (
      <div className="rounded-xl border border-border bg-card p-4 space-y-3">
        {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-14 w-full" />)}
      </div>
    );
  }
  return <Skeleton className="h-[640px] w-full rounded-xl" />;
}

export default function CalendarPage() {
  const { user } = useAuth();
  const { can } = usePermission();
  const canCreate = can("calendar.create");
  const [searchParams, setSearchParams] = useSearchParams();

  const view = VIEW_KEYS.includes(searchParams.get("view")) ? searchParams.get("view") : "month";
  const date = useMemo(() => parseDateParam(searchParams.get("date")), [searchParams]);

  const [filters, setFilters] = useState({ category: "", mine: false, showCancelled: false });
  const [data, setData] = useState(null);
  const [dataView, setDataView] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const requestId = useRef(0);
  const backgroundLoad = useRef(false);

  const [selected, setSelected] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [defaultStart, setDefaultStart] = useState(null);

  const navigate = useCallback((next) => {
    setSearchParams((params) => {
      if (next.view) params.set("view", next.view);
      if (next.date) setDateParam(params, next.date);
      return params;
    }, { replace: false });
  }, [setSearchParams]);

  // ------------------------- data -------------------------

  useEffect(() => {
    const id = ++requestId.current;
    const controller = new AbortController();
    // Background refreshes keep the current view on screen instead of dimming it.
    const background = backgroundLoad.current;
    backgroundLoad.current = false;
    if (!background) {
      setLoading(true);
      setError(null);
    }
    const [url, params] = requestFor(view, date, filters);
    api.get(url, { params, signal: controller.signal })
      .then(({ data: body }) => {
        if (id !== requestId.current) return;
        setData(body);
        setDataView(view);
      })
      .catch((e) => {
        if (id !== requestId.current || controller.signal.aborted || background) return;
        setError(formatApiError(e));
      })
      .finally(() => { if (id === requestId.current) setLoading(false); });
    return () => controller.abort();
  }, [view, date, filters, reloadKey]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  // Live data: refresh quietly, but never while a dialog is open or extra agenda pages are loaded.
  useLiveRefresh(() => {
    if (formOpen || detailsOpen || loadingMore || (view === "agenda" && data?.page > 1)) return;
    backgroundLoad.current = true;
    reload();
  }, 60000);

  const loadMore = async () => {
    const [url, params] = requestFor("agenda", date, filters, data.page + 1);
    // A view / date / filter change while this page loads starts a new request; drop this page then.
    const id = requestId.current;
    setLoadingMore(true);
    try {
      const { data: next } = await api.get(url, { params });
      if (id !== requestId.current) return;
      setData((prev) => ({ ...next, items: [...prev.items, ...next.items] }));
    } catch (e) {
      if (id === requestId.current) toast.error(formatApiError(e));
    } finally {
      setLoadingMore(false);
    }
  };

  // ------------------------- deep links -------------------------

  // ?event=<id> (from notifications) opens that event; ?create=event (quick create) opens the form.
  // The URL is cleaned in a single update — separate updates would each start from a stale copy
  // and put the parameter back.
  const handledLink = useRef(null);
  useEffect(() => {
    const eventId = searchParams.get("event");
    const create = searchParams.get("create");
    if (!eventId && !create) return;
    const key = searchParams.toString();
    if (handledLink.current === key) return;
    handledLink.current = key;

    const clearLink = (focusDate) => setSearchParams((params) => {
      params.delete("event");
      params.delete("create");
      if (focusDate) setDateParam(params, focusDate);
      return params;
    }, { replace: true });

    if (create) {
      clearLink();
      if (canCreate) createOnDay(new Date());
      return;
    }
    api.get(`/calendar/events/${eventId}`)
      .then(({ data: ev }) => {
        setSelected(ev);
        setDetailsOpen(true);
        clearLink(startOfDay(new Date(ev.start_time)));
      })
      .catch(() => {
        clearLink();
        toast.error("This event no longer exists or isn't shared with you.");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // ------------------------- actions -------------------------

  function openCreate(at) {
    setEditing(null);
    setDefaultStart(at);
    setFormOpen(true);
  }

  // Clicking a day starts the event at a sensible hour; clicking a time slot uses that exact time.
  const createOnDay = canCreate ? (day) => openCreate(setHours(startOfDay(day), DEFAULT_EVENT_HOUR)) : () => {};
  const createAtTime = canCreate ? openCreate : () => {};

  const selectEvent = (ev) => {
    setSelected(ev);
    setDetailsOpen(true);
  };

  const editEvent = (ev) => {
    setDetailsOpen(false);
    setEditing(ev);
    setFormOpen(true);
  };

  const onSaved = (ev) => {
    setFormOpen(false);
    setSelected(ev);
    reload();
  };

  const onChanged = (ev) => {
    if (ev) setSelected(ev);
    else setDetailsOpen(false);
    reload();
  };

  // Keyboard: T today, ←/→ previous/next, M/W/D/A switch view, N new event.
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || formOpen || detailsOpen) return;
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.target?.isContentEditable) return;
      const key = e.key.toLowerCase();
      const viewKey = { m: "month", w: "week", d: "day", a: "agenda" }[key];
      if (key === "t") navigate({ date: new Date() });
      else if (e.key === "ArrowLeft") navigate({ date: shiftDate(date, view, -1) });
      else if (e.key === "ArrowRight") navigate({ date: shiftDate(date, view, 1) });
      else if (viewKey) navigate({ view: viewKey });
      else if (key === "n" && canCreate) createOnDay(date);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ------------------------- render -------------------------

  const ready = data && dataView === view && !error;
  const count = ready ? (view === "agenda" ? data.total : data.events.length) : null;

  const body = () => {
    if (error) {
      return (
        <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-10 text-center">
          <AlertTriangle className="h-6 w-6 text-destructive mx-auto" />
          <div className="font-display text-[15px] font-semibold mt-3">Couldn't load the calendar</div>
          <div className="text-[13px] text-muted-foreground mt-1">{error}</div>
          <Button variant="outline" size="sm" className="mt-4 gap-1.5" onClick={reload}>
            <RefreshCw className="h-3.5 w-3.5" /> Try again
          </Button>
        </div>
      );
    }
    if (!ready) return <ViewSkeleton view={view} />;
    const common = { onSelectEvent: selectEvent };
    return (
      <div className={cn("transition-opacity", loading && "opacity-60 pointer-events-none")}>
        {view === "month" && (
          <MonthView {...common} data={data} anchorDate={date} onCreateAt={createOnDay}
                     onSelectDay={(d) => navigate({ view: "day", date: d })} />
        )}
        {view === "week" && (
          <TimeGridView {...common} days={weekDays(date)} events={data.events} onCreateAt={createAtTime}
                        onSelectDay={(d) => navigate({ view: "day", date: d })} />
        )}
        {view === "day" && (
          <TimeGridView {...common} days={[date]} events={data.events} onCreateAt={createAtTime} onSelectDay={() => {}} />
        )}
        {view === "agenda" && (
          <AgendaView {...common} data={data} from={date} onLoadMore={loadMore} loadingMore={loadingMore}
                      onCreate={canCreate ? () => createOnDay(date) : null} />
        )}
      </div>
    );
  };

  return (
    <div data-testid="calendar-page">
      <PageHeader
        eyebrow="Workspace"
        title="Calendar"
        description="Company meetings, launches and milestones — shown in your local time."
        actions={canCreate && (
          <Button onClick={() => createOnDay(date)} className="gap-1.5 font-medium" data-testid="calendar-new-event">
            <Plus className="h-4 w-4" /> New event
          </Button>
        )}
      />

      <div className="grid gap-6 xl:grid-cols-[252px_minmax(0,1fr)]">
        <aside className="order-2 xl:order-1 grid gap-5 sm:grid-cols-2 xl:grid-cols-1 content-start">
          <div className="rounded-xl border border-border bg-card self-start">
            <MiniCalendar
              mode="single"
              selected={date}
              month={date}
              onMonthChange={(m) => navigate({ date: m })}
              onSelect={(d) => d && navigate({ date: d })}
              weekStartsOn={WEEK_STARTS_ON}
              className="mx-auto w-fit"
            />
          </div>

          <div className="rounded-xl border border-border bg-card p-4 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="cal-mine" className="text-[13px] cursor-pointer">Only my events</Label>
              <Switch id="cal-mine" checked={filters.mine} onCheckedChange={(v) => setFilters((f) => ({ ...f, mine: v }))} />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="cal-cancelled" className="text-[13px] cursor-pointer">Show cancelled</Label>
              <Switch id="cal-cancelled" checked={filters.showCancelled} onCheckedChange={(v) => setFilters((f) => ({ ...f, showCancelled: v }))} />
            </div>
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Categories</div>
                {filters.category && (
                  <button type="button" className="text-[11.5px] text-primary hover:underline" onClick={() => setFilters((f) => ({ ...f, category: "" }))}>
                    Show all
                  </button>
                )}
              </div>
              <ul className="space-y-0.5">
                {CATEGORIES.map((c) => {
                  const active = filters.category === c;
                  const dimmed = filters.category && !active;
                  return (
                    <li key={c}>
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => setFilters((f) => ({ ...f, category: active ? "" : c }))}
                        className={cn(
                          "w-full flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] text-left transition-colors hover:bg-muted",
                          active && "bg-muted font-medium",
                          dimmed && "opacity-50",
                        )}
                      >
                        <span className={cn("h-2.5 w-2.5 rounded-sm", categoryStyle(c).dot)} />
                        {c}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </aside>

        <section className="min-w-0 space-y-4 order-1 xl:order-2">
          <div className="flex flex-col lg:flex-row lg:items-center gap-3 justify-between">
            <div className="flex items-center gap-2 min-w-0">
              <Button variant="outline" size="sm" onClick={() => navigate({ date: new Date() })} data-testid="calendar-today">Today</Button>
              <TooltipProvider delayDuration={300}>
                <div className="flex">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Previous"
                              onClick={() => navigate({ date: shiftDate(date, view, -1) })} data-testid="calendar-prev">
                        <ChevronLeft className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Previous (←)</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Next"
                              onClick={() => navigate({ date: shiftDate(date, view, 1) })} data-testid="calendar-next">
                        <ChevronRight className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Next (→)</TooltipContent>
                  </Tooltip>
                </div>
              </TooltipProvider>
              <div className="min-w-0">
                <h2 className="font-display text-lg font-semibold tracking-tight truncate" data-testid="calendar-range-title">
                  {rangeTitle(date, view)}
                </h2>
                <div className="text-[11.5px] text-muted-foreground flex items-center gap-1.5 h-4">
                  {loading && <Loader2 className="h-3 w-3 animate-spin" />}
                  {count !== null && !loading && `${count} event${count === 1 ? "" : "s"}${data?.truncated ? " (showing first 2000)" : ""}`}
                </div>
              </div>
            </div>
            <Tabs value={view} onValueChange={(v) => navigate({ view: v })}>
              <TabsList className="bg-muted/60 p-1">
                {VIEWS.map((v) => (
                  <TabsTrigger key={v.key} value={v.key} data-testid={`calendar-view-${v.key}`}>{v.label}</TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>

          {body()}

          {ready && view !== "agenda" && count === 0 && (
            <p className="text-center text-[12.5px] text-muted-foreground flex items-center justify-center gap-1.5">
              <CalendarDays className="h-3.5 w-3.5" />
              Nothing scheduled here{canCreate ? " — click any day or time slot to add an event." : "."}
            </p>
          )}
        </section>
      </div>

      <EventDetailsDialog
        event={selected}
        open={detailsOpen}
        onOpenChange={setDetailsOpen}
        onEdit={editEvent}
        onChanged={onChanged}
        user={user}
        can={can}
      />
      <EventDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        event={editing}
        defaultStart={defaultStart}
        onSaved={onSaved}
        user={user}
      />
    </div>
  );
}
