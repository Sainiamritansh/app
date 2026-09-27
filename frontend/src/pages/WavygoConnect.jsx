import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader, EmptyState } from "@/components/module/ModulePrimitives";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MessagesSquare, Hash, Users2, Megaphone, Plus, Send, Search, Lock, Building2, ShieldCheck, UserPlus } from "lucide-react";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { usePermission } from "@/hooks/usePermission";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "date-fns";

const KIND_ICON = { channel: Hash, dm: MessagesSquare, group: Users2, announcement: Megaphone };
const KIND_LABEL = { channel: "Channel", group: "Group", announcement: "Announcement channel" };

const HIGH_ROLES = ["Founder", "Admin", "Manager"];
const HIGH_DESIG_KEYWORDS = [
  "founder", "ceo", "cto", "coo", "cfo", "chief", "director", "head",
  "president", "vp", "vice president", "manager", "lead", "general manager"
];

function isHighDesignation(u) {
  if (!u) return false;
  if (HIGH_ROLES.includes(u.role)) return true;
  const d = (u.designation || "").toLowerCase();
  return HIGH_DESIG_KEYWORDS.some(k => d.includes(k));
}

function initials(name) { return (name || "?").split(" ").map(s => s[0]).filter(Boolean).slice(0, 2).join("").toUpperCase(); }

const NO_IDS = [];

/* Checkbox list of DM-eligible users (from /connect/users) for picking group members. */
function MemberPicker({ users, selected, onChange, exclude = NO_IDS }) {
  const [search, setSearch] = useState("");
  const list = useMemo(() => {
    const t = search.trim().toLowerCase();
    return users.filter(u => !exclude.includes(u.id) && (!t ||
      [u.name, u.email, u.role, u.designation, u.department].some(v => (v || "").toLowerCase().includes(t))));
  }, [users, exclude, search]);
  const toggle = (id) => onChange(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id]);
  return (
    <div>
      <Label>Members{selected.length > 0 && <span className="text-muted-foreground font-normal"> · {selected.length} selected</span>}</Label>
      <div className="relative mt-1">
        <Search className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input placeholder="Search people…" value={search} onChange={(e) => setSearch(e.target.value)} className="h-8 pl-8 text-[13px]" />
      </div>
      <div className="mt-2 max-h-[220px] overflow-y-auto scrollbar-thin rounded-md border border-border divide-y divide-border" data-testid="connect-member-picker">
        {list.length === 0 ? (
          <div className="text-center py-6 text-sm text-muted-foreground">No matching members found.</div>
        ) : list.map(u => (
          <label key={u.id} className="flex items-center gap-3 px-3 py-2 hover:bg-muted/70 cursor-pointer">
            <Checkbox checked={selected.includes(u.id)} onCheckedChange={() => toggle(u.id)} />
            <Avatar className="h-7 w-7">
              <AvatarImage src={u.photo || undefined} />
              <AvatarFallback className="text-[9px] bg-wavygo-100 text-wavygo-800">{initials(u.name)}</AvatarFallback>
            </Avatar>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-medium truncate">{u.name}</div>
              <div className="text-[11px] text-muted-foreground truncate">{u.designation || u.role}{u.department ? ` · ${u.department}` : ""}</div>
            </div>
          </label>
        ))}
      </div>
    </div>
  );
}

export default function WavygoConnect() {
  const { user } = useAuth();
  const { can } = usePermission();
  const canCreateChannel = can("connect.create_channel");
  const canCreateAnnouncement = can("connect.create_announcement");
  const canPostAnnouncement = can("connect.post_announcement");
  const [channels, setChannels] = useState([]);
  const [users, setUsers] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [dmOpen, setDmOpen] = useState(false);
  const [dmSearch, setDmSearch] = useState("");
  const [form, setForm] = useState({ name: "", kind: "channel", description: "", members: [] });
  const [addOpen, setAddOpen] = useState(false);
  const [addSel, setAddSel] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);   // a create / add-members request is in flight
  const sendingRef = useRef(false);          // blocks double sends (Enter pressed twice)
  const scrollRef = useRef(null);
  const paneRef = useRef(null);
  const revealRef = useRef(false);      // bring the pane into view once the picked channel's messages render
  const activeIdRef = useRef(null);
  const msgSigRef = useRef("");        // "<channel>:<count>:<last id>" of the rendered messages
  const scrollNextRef = useRef(false); // scroll to bottom after the next messages render

  // Open a channel; on the stacked (mobile) layout bring the message pane + composer into view.
  function revealPane() {
    paneRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }
  function selectChannel(id) {
    setActiveId(id);
    if (window.matchMedia("(max-width: 1023px)").matches) {
      revealRef.current = id !== activeIdRef.current;  // same channel: no re-render to wait for
      requestAnimationFrame(revealPane);  // immediate feedback; repeated after the messages render
    }
  }

  async function loadChannels(selectId = null, silent = false) {
    try {
      const { data } = await api.get("/connect/channels");
      const openId = selectId || activeIdRef.current;
      // the open channel is marked read as messages arrive, so never badge it
      setChannels(data.map(c => (c.id === openId ? { ...c, unread: 0 } : c)));
      if (selectId) selectChannel(selectId);
      else setActiveId(prev => prev || data[0]?.id || null);
    } catch (e) { if (!silent) toast.error(formatApiError(e)); }
  }

  async function loadUsers() {
    try {
      const { data } = await api.get("/connect/users");
      setUsers(data || []);
    } catch (e) { toast.error(formatApiError(e)); }
  }

  // Load once on mount, then refresh the channel list every 15 s.
  useEffect(() => {
    loadChannels(); loadUsers();
    const t = setInterval(() => loadChannels(null, true), 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (dmOpen) {
      loadUsers();
      setDmSearch("");
    }
  }, [dmOpen]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (createOpen || addOpen) loadUsers(); }, [createOpen, addOpen]);

  function markRead(id) {
    api.post(`/connect/channels/${id}/read`).catch(() => { /* retried on the next change */ });
    setChannels(cs => cs.map(c => (c.id === id && c.unread ? { ...c, unread: 0 } : c)));
  }

  function isNearBottom() {
    const el = scrollRef.current;
    return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  // Only re-render when the channel's messages changed; follow new messages only when the reader
  // is already near the bottom (or just opened the channel / sent a message).
  async function loadMessages(id, { scroll = false, silent = false } = {}) {
    try {
      const { data } = await api.get(`/connect/channels/${id}/messages`);
      if (id !== activeIdRef.current) return;
      const sig = `${id}:${data.length}:${data[data.length - 1]?.id || ""}`;
      if (sig === msgSigRef.current) return;
      msgSigRef.current = sig;
      scrollNextRef.current = scroll || isNearBottom();
      setMessages(data);
      markRead(id);
    } catch (e) { if (!silent) toast.error(formatApiError(e)); }
  }
  useEffect(() => {
    activeIdRef.current = activeId;
    msgSigRef.current = "";
    if (activeId) {
      loadMessages(activeId, { scroll: true });
      const t = setInterval(() => loadMessages(activeId, { silent: true }), 5000);
      return () => clearInterval(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  useEffect(() => {
    if (scrollNextRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    scrollNextRef.current = false;
    if (revealRef.current) { revealRef.current = false; revealPane(); }
  }, [messages]);

  async function createChannel() {
    if (!form.name.trim() || busy) return;
    setBusy(true);
    try {
      const members = form.kind === "group" ? form.members : [];
      const { data } = await api.post("/connect/channels", { name: form.name.trim(), kind: form.kind, description: form.description.trim(), members });
      toast.success(`${KIND_LABEL[form.kind] || "Channel"} created`);
      setCreateOpen(false); setForm({ name: "", kind: "channel", description: "", members: [] });
      loadChannels(data.id);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  }

  async function addMembers() {
    if (!activeId || addSel.length === 0 || busy) return;
    setBusy(true);
    try {
      await api.post(`/connect/channels/${activeId}/members`, { member_ids: addSel });
      toast.success(`${addSel.length} member${addSel.length > 1 ? "s" : ""} added`);
      setAddOpen(false); setAddSel([]);
      loadChannels();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  }

  async function openDm(peerId) {
    try {
      const { data } = await api.post(`/connect/dm/${peerId}`);
      setDmOpen(false);
      loadChannels(data.id);
    } catch (e) { toast.error(formatApiError(e)); }
  }

  async function send() {
    if (!text.trim() || !activeId || sendingRef.current) return;
    sendingRef.current = true;
    try {
      await api.post(`/connect/channels/${activeId}/messages`, { body: text.trim() });
      setText("");
      loadMessages(activeId, { scroll: true });
      loadChannels(null, true);
    } catch (e) { toast.error(formatApiError(e)); } finally { sendingRef.current = false; }
  }

  const active = channels.find(c => c.id === activeId);
  const canAddMembers = active?.kind === "group" &&
    (active.created_by === user?.id || user?.role === "Founder" || user?.role === "Admin");

  const grouped = useMemo(() => {
    const g = { announcement: [], channel: [], group: [], dm: [] };
    for (const c of channels) {
      if (q && !((c.display_name || c.name) + " " + (c.description || "")).toLowerCase().includes(q.toLowerCase())) continue;
      (g[c.kind] || (g[c.kind] = [])).push(c);
    }
    return g;
  }, [channels, q]);

  const { deptMembers, leadershipMembers, otherMembers } = useMemo(() => {
    const qLower = dmSearch.trim().toLowerCase();
    const currDept = (user?.department || "").trim().toLowerCase();
    const isFounderOrAdmin = user?.role === "Founder" || user?.role === "Admin";

    const base = users.filter(u => u.id !== user?.id && u.status !== "deactivated" && u.is_active !== false);

    const matchesSearch = (u) => {
      if (!qLower) return true;
      return (
        (u.name || "").toLowerCase().includes(qLower) ||
        (u.role || "").toLowerCase().includes(qLower) ||
        (u.designation || "").toLowerCase().includes(qLower) ||
        (u.department || "").toLowerCase().includes(qLower) ||
        (u.email || "").toLowerCase().includes(qLower)
      );
    };

    const dept = [];
    const leadership = [];
    const others = [];

    for (const u of base) {
      if (!matchesSearch(u)) continue;
      const uDept = (u.department || "").trim().toLowerCase();
      const isSameDept = Boolean(currDept) && Boolean(uDept) && uDept === currDept;
      const isHighDesig = isHighDesignation(u);

      if (isSameDept) {
        dept.push(u);
      } else if (isHighDesig) {
        leadership.push(u);
      } else if (isFounderOrAdmin) {
        others.push(u);
      }
    }

    return { deptMembers: dept, leadershipMembers: leadership, otherMembers: others };
  }, [users, user, dmSearch]);

  function renderUserRow(u) {
    const isLeadership = isHighDesignation(u);
    return (
      <li key={u.id}>
        <button
          onClick={() => openDm(u.id)}
          className="w-full flex items-center gap-3 py-2.5 px-2 hover:bg-muted/70 rounded-md text-left transition-colors group"
        >
          <div className="relative shrink-0">
            <Avatar className="h-8 w-8">
              <AvatarImage src={u.photo || undefined} />
              <AvatarFallback className="text-[10px] bg-wavygo-100 text-wavygo-800">
                {initials(u.name)}
              </AvatarFallback>
            </Avatar>
            <span
              className={cn(
                "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-background",
                u.online ? "bg-emerald-500" : "bg-slate-300"
              )}
            />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[13px] font-medium text-foreground truncate group-hover:text-primary transition-colors">
                {u.name}
              </span>
              {isLeadership && (
                <Badge variant="secondary" className="text-[9.5px] px-1.5 py-0 h-4 bg-amber-500/10 text-amber-700 dark:text-amber-400 font-normal border-amber-500/20">
                  Leadership
                </Badge>
              )}
            </div>
            <div className="text-[11px] text-muted-foreground truncate">
              {u.designation || u.role}
              {u.department ? ` · ${u.department}` : ""}
            </div>
          </div>

          <Badge variant="outline" className="text-[10.5px] shrink-0 font-normal text-muted-foreground">
            {u.role}
          </Badge>
        </button>
      </li>
    );
  }

  return (
    <div data-testid="connect-page">
      <PageHeader
        eyebrow="Module"
        title="WavyGo Connect"
        description="Internal channels, announcements, groups and direct messages — all your team communication in one place."
        actions={
          <>
            <Button variant="outline" onClick={() => setDmOpen(true)}><MessagesSquare className="h-4 w-4 mr-1.5" /> New DM</Button>
            {canCreateChannel && (
              <Button onClick={() => setCreateOpen(true)} data-testid="connect-create-btn"><Plus className="h-4 w-4 mr-1.5" /> New channel</Button>
            )}
          </>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 min-h-[560px]">
        {/* Channel list */}
        <Card className="border-border p-3 flex flex-col">
          <div className="relative">
            <Search className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} className="h-8 pl-8 text-[13px]" />
          </div>
          {/* Radix renders the viewport content as display:table, which lets long previews widen the list
              past the card and push unread badges out of view; force a block so rows truncate. */}
          <ScrollArea className="mt-3 flex-1 [&_[data-radix-scroll-area-viewport]>div]:!block">
            {["announcement", "channel", "group", "dm"].map(kind => (
              (grouped[kind] || []).length > 0 && (
                <div key={kind} className="mb-4">
                  <div className="text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground px-2 mb-1.5">
                    {kind === "dm" ? "Direct Messages" : kind === "announcement" ? "Announcements" : kind === "group" ? "Groups" : "Channels"}
                  </div>
                  <ul className="space-y-0.5">
                    {(grouped[kind] || []).map(c => {
                      const Icon = KIND_ICON[c.kind] || Hash;
                      const isActive = c.id === activeId;
                      const displayName = c.display_name || c.name;
                      return (
                        <li key={c.id}>
                          <button onClick={() => selectChannel(c.id)}
                                  className={cn(
                                    "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors",
                                    isActive ? "bg-primary/10 text-foreground font-medium" : "text-foreground/80 hover:bg-muted"
                                  )}>
                            {c.kind === "dm" ? (
                              <Avatar className="h-5 w-5">
                                <AvatarImage src={c.peer_photo || undefined} />
                                <AvatarFallback className="text-[8px] bg-wavygo-100 text-wavygo-800">{initials(displayName)}</AvatarFallback>
                              </Avatar>
                            ) : <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                            <span className="flex-1 min-w-0 text-left">
                              <span className={cn("block truncate", c.unread > 0 && "font-semibold text-foreground")}>{displayName}</span>
                              {c.last_body && <span className="block truncate text-[11px] font-normal text-muted-foreground">{c.last_body}</span>}
                            </span>
                            {c.unread > 0 && !isActive && (
                              <Badge className="h-4 min-w-4 px-1 text-[10px] rounded-full justify-center shrink-0" data-testid="connect-unread-badge">
                                {c.unread > 99 ? "99+" : c.unread}
                              </Badge>
                            )}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )
            ))}
          </ScrollArea>
        </Card>

        {/* Message pane */}
        <Card ref={paneRef} className="border-border flex flex-col overflow-hidden scroll-mt-20">
          {!active ? (
            <EmptyState icon={MessagesSquare} title="Select a channel" description="Pick a channel from the list to start chatting." />
          ) : (
            <>
              <div className="px-5 py-3 border-b border-border flex items-center gap-3">
                {active.kind === "dm" ? (
                  <>
                    <Avatar className="h-8 w-8"><AvatarImage src={active.peer_photo || undefined} /><AvatarFallback className="text-[10px] bg-wavygo-100 text-wavygo-800">{initials(active.display_name || active.name)}</AvatarFallback></Avatar>
                    <div className="flex-1">
                      <div className="font-display text-[15px] font-semibold">{active.display_name || active.name}</div>
                      <div className="text-[11px] text-muted-foreground flex items-center gap-1.5">
                        <span className={cn("h-1.5 w-1.5 rounded-full", active.peer_online ? "bg-emerald-500" : "bg-slate-400")} />
                        {active.peer_online ? "Online" : "Offline"} · {active.peer_role}
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="h-8 w-8 rounded-md bg-primary/10 text-primary flex items-center justify-center">
                      {(() => { const Icon = KIND_ICON[active.kind] || Hash; return <Icon className="h-4 w-4" />; })()}
                    </div>
                    <div className="flex-1">
                      <div className="font-display text-[15px] font-semibold flex items-center gap-2">
                        {active.name}
                        {active.kind === "group" && <Badge variant="secondary" className="text-[10px]"><Lock className="h-2.5 w-2.5 mr-1" />Private</Badge>}
                        {active.kind === "announcement" && <Badge className="bg-info/10 text-info hover:bg-info/10 text-[10px]">Announcement</Badge>}
                      </div>
                      {active.description && <div className="text-[11.5px] text-muted-foreground">{active.description}</div>}
                    </div>
                    {canAddMembers && (
                      <Button variant="outline" size="sm" onClick={() => { setAddSel([]); setAddOpen(true); }} data-testid="connect-add-members-btn">
                        <UserPlus className="h-4 w-4 mr-1.5" /> Add members
                      </Button>
                    )}
                  </>
                )}
              </div>

              <div ref={scrollRef} className="flex-1 overflow-y-auto scrollbar-thin px-5 py-4 space-y-4 min-h-[300px] max-h-[520px]">
                {messages.length === 0 && <div className="text-center text-sm text-muted-foreground py-16">No messages yet. Say hello 👋</div>}
                {messages.map(m => {
                  const mine = m.sender_id === user?.id;
                  return (
                    <div key={m.id} className={cn("flex gap-2.5", mine && "flex-row-reverse")}>
                      <Avatar className="h-7 w-7 shrink-0"><AvatarImage src={m.sender_photo || undefined} /><AvatarFallback className="text-[9px] bg-wavygo-100 text-wavygo-800">{initials(m.sender_name)}</AvatarFallback></Avatar>
                      <div className={cn("max-w-[70%]", mine && "text-right")}>
                        <div className={cn("flex items-baseline gap-2 mb-0.5", mine && "flex-row-reverse")}>
                          <span className="text-[12px] font-medium">{m.sender_name}</span>
                          <span className="text-[10.5px] text-muted-foreground">{(() => { try { return formatDistanceToNow(new Date(m.created_at), { addSuffix: true }); } catch { return ""; } })()}</span>
                        </div>
                        <div className={cn("inline-block rounded-lg px-3 py-2 text-[13.5px] leading-relaxed", mine ? "bg-primary text-primary-foreground" : "bg-muted text-foreground")}>
                          {m.body}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {(active.kind !== "announcement" || canPostAnnouncement) && (
                <div className="border-t border-border px-4 py-3 flex items-center gap-2">
                  <Input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), send())}
                         placeholder={`Message ${active.kind === "dm" ? active.display_name || active.name : "#" + active.name}`}
                         className="h-10" data-testid="connect-message-input" />
                  <Button onClick={send} disabled={!text.trim()} data-testid="connect-send-btn"><Send className="h-4 w-4" /></Button>
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      {/* New channel dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle className="font-display">New channel</DialogTitle><DialogDescription>Channels are visible to everyone. Groups are private.</DialogDescription></DialogHeader>
          <div className="space-y-3">
            <div><Label>Name</Label><Input value={form.name} onChange={(e) => setForm(s => ({ ...s, name: e.target.value }))} placeholder="e.g. patna-ops" /></div>
            <div>
              <Label>Kind</Label>
              <Select value={form.kind} onValueChange={(v) => setForm(s => ({ ...s, kind: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="channel">Channel — public</SelectItem>
                  <SelectItem value="group">Group — private</SelectItem>
                  {canCreateAnnouncement && <SelectItem value="announcement">Announcement — broadcast</SelectItem>}
                </SelectContent>
              </Select>
            </div>
            <div><Label>Description</Label><Textarea rows={2} value={form.description} onChange={(e) => setForm(s => ({ ...s, description: e.target.value }))} /></div>
            {form.kind === "group" && (
              <MemberPicker users={users} selected={form.members} onChange={(members) => setForm(s => ({ ...s, members }))} />
            )}
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button><Button onClick={createChannel} disabled={!form.name.trim() || busy} data-testid="connect-create-submit">Create</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add group members dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="font-display">Add members</DialogTitle>
            <DialogDescription>{active ? `Add people to ${active.name}.` : ""}</DialogDescription>
          </DialogHeader>
          <MemberPicker users={users} selected={addSel} onChange={setAddSel} exclude={active?.members || NO_IDS} />
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button onClick={addMembers} disabled={addSel.length === 0 || busy} data-testid="connect-add-members-submit">Add</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* New DM dialog */}
      <Dialog open={dmOpen} onOpenChange={setDmOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="font-display flex items-center gap-2">
              <MessagesSquare className="h-5 w-5 text-primary" />
              Start a direct message
            </DialogTitle>
            <DialogDescription>
              {user?.department
                ? `Connect with members of ${user.department} or company leadership.`
                : "Connect with colleagues and company leadership."}
            </DialogDescription>
          </DialogHeader>

          <div className="relative mt-1">
            <Search className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search by name, role, designation or department…"
              value={dmSearch}
              onChange={(e) => setDmSearch(e.target.value)}
              className="h-9 pl-8 text-[13px]"
            />
          </div>

          <div className="max-h-[380px] overflow-y-auto scrollbar-thin -mx-6 px-6 space-y-4 pt-1">
            {deptMembers.length === 0 && leadershipMembers.length === 0 && otherMembers.length === 0 ? (
              <div className="text-center py-8 text-sm text-muted-foreground">
                No matching members found.
              </div>
            ) : (
              <>
                {deptMembers.length > 0 && (
                  <div>
                    <div className="text-[10.5px] uppercase tracking-[0.14em] font-semibold text-muted-foreground flex items-center gap-1.5 mb-1.5 px-1">
                      <Building2 className="h-3.5 w-3.5 text-primary" />
                      {user?.department ? `${user.department} Department` : "My Department"}
                      <span className="text-[10px] text-muted-foreground font-normal">({deptMembers.length})</span>
                    </div>
                    <ul className="divide-y divide-border">
                      {deptMembers.map(u => renderUserRow(u))}
                    </ul>
                  </div>
                )}

                {leadershipMembers.length > 0 && (
                  <div>
                    <div className="text-[10.5px] uppercase tracking-[0.14em] font-semibold text-muted-foreground flex items-center gap-1.5 mb-1.5 px-1">
                      <ShieldCheck className="h-3.5 w-3.5 text-amber-500" />
                      Company Leadership & High Designation
                      <span className="text-[10px] text-muted-foreground font-normal">({leadershipMembers.length})</span>
                    </div>
                    <ul className="divide-y divide-border">
                      {leadershipMembers.map(u => renderUserRow(u))}
                    </ul>
                  </div>
                )}

                {otherMembers.length > 0 && (
                  <div>
                    <div className="text-[10.5px] uppercase tracking-[0.14em] font-semibold text-muted-foreground flex items-center gap-1.5 mb-1.5 px-1">
                      <Users2 className="h-3.5 w-3.5 text-muted-foreground" />
                      Other Team Members
                      <span className="text-[10px] text-muted-foreground font-normal">({otherMembers.length})</span>
                    </div>
                    <ul className="divide-y divide-border">
                      {otherMembers.map(u => renderUserRow(u))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
