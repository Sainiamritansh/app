import { useEffect, useState } from "react";
import { addDays, format, parseISO, subDays, subMonths } from "date-fns";
import { CalendarRange, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

export const PRESETS = [
  { key: "7d", label: "7D" },
  { key: "30d", label: "30D" },
  { key: "90d", label: "90D" },
  { key: "12m", label: "12M" },
];

const ALL = "__all__";

/** Inclusive IST date range for a preset, anchored on the server's "today". */
export function presetRange(key, today) {
  const end = today ? parseISO(today) : new Date();
  const from = {
    "7d": subDays(end, 6),
    "30d": subDays(end, 29),
    "90d": subDays(end, 89),
    "12m": addDays(subMonths(end, 12), 1),
  }[key] || subDays(end, 29);
  return { from: format(from, "yyyy-MM-dd"), to: format(end, "yyyy-MM-dd") };
}

export function rangeLabel(range) {
  try {
    const f = parseISO(range.from);
    const t = parseISO(range.to);
    const sameYear = f.getFullYear() === t.getFullYear();
    return `${format(f, sameYear ? "d MMM" : "d MMM yyyy")} – ${format(t, "d MMM yyyy")}`;
  } catch {
    return `${range.from} – ${range.to}`;
  }
}

function CustomRange({ range, active, onApply }) {
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState(range.from);
  const [to, setTo] = useState(range.to);
  useEffect(() => { if (open) { setFrom(range.from); setTo(range.to); } }, [open, range.from, range.to]);
  const invalid = !from || !to || from > to;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant={active ? "secondary" : "ghost"} size="sm"
          className={cn("h-8 px-2.5 text-xs", active && "text-foreground")}
          data-testid="analytics-custom-range"
        >
          <CalendarRange className="h-3.5 w-3.5 mr-1" />Custom
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label htmlFor="ana-from" className="text-xs">From</Label>
              <Input id="ana-from" type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} className="h-9 text-xs" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="ana-to" className="text-xs">To</Label>
              <Input id="ana-to" type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} className="h-9 text-xs" />
            </div>
          </div>
          {invalid && <div className="text-[11px] text-destructive">Pick a start date on or before the end date.</div>}
          <Button size="sm" className="w-full h-8 text-xs" disabled={invalid}
            onClick={() => { onApply({ from, to }); setOpen(false); }}>
            Apply range
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export default function FilterBar({
  preset, range, onPreset, onCustom, cities, city, onCity, showCity,
  departments, department, onDepartment, showDepartment, departmentLocked, onRefresh, refreshing,
}) {
  return (
    <div className="flex flex-col lg:flex-row lg:items-center gap-2 lg:gap-3 rounded-xl border border-border bg-card p-2 sm:p-3" data-testid="analytics-filters">
      <div className="flex items-center gap-1 flex-wrap">
        <div className="inline-flex items-center rounded-md bg-muted p-0.5">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => onPreset(p.key)}
              aria-pressed={preset === p.key}
              data-testid={`analytics-preset-${p.key}`}
              className={cn(
                "h-7 px-2.5 rounded text-xs font-medium transition-colors",
                preset === p.key ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        <CustomRange range={range} active={preset === "custom"} onApply={onCustom} />
        <span className="text-xs text-muted-foreground px-1 hidden sm:inline">{rangeLabel(range)}</span>
      </div>

      <div className="flex items-center gap-2 lg:ml-auto min-w-0">
        {showCity && (
          <Select value={city || ALL} onValueChange={(v) => onCity(v === ALL ? "" : v)}>
            <SelectTrigger className="h-8 w-full sm:w-44 text-xs" data-testid="analytics-city" aria-label="City filter">
              <SelectValue placeholder="All cities" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All cities</SelectItem>
              {(cities || []).map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        {showDepartment && (
          <Select value={department || ALL} onValueChange={(v) => onDepartment(v === ALL ? "" : v)} disabled={!!departmentLocked}>
            <SelectTrigger className="h-8 w-full sm:w-44 text-xs" data-testid="analytics-department" aria-label="Department filter">
              <SelectValue placeholder="All departments" />
            </SelectTrigger>
            <SelectContent>
              {!departmentLocked && <SelectItem value={ALL}>All departments</SelectItem>}
              {(departments || []).map((d) => <SelectItem key={d} value={d}>{d}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Button variant="ghost" size="sm" className="h-8 w-8 p-0 shrink-0" onClick={onRefresh} aria-label="Refresh analytics" data-testid="analytics-refresh">
          <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
        </Button>
      </div>
      <span className="text-[11px] text-muted-foreground px-1 sm:hidden">{rangeLabel(range)} · IST</span>
    </div>
  );
}
