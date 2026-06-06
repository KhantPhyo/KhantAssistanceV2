import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Plus, ChevronRight, Repeat, Pencil, Trash2 } from "lucide-react";
import { format } from "date-fns";
import toast from "react-hot-toast";

type Job = {
  id: number; code: string; title: string; description: string; report_type: string;
  deadline_at?: string; status: string; created_at: string; created_via?: string;
  recurrence?: string; is_template?: boolean; next_spawn_at?: string;
  parent_template_id?: number | null;
  action?: string;  // "one-time" | "daily" | "weekly" | "monthly" | "template:<cadence>"
  assignments: { id: number; assistant_id: number; assistant_name?: string; status: string }[];
};

const ACTION_BADGE: Record<string, string> = {
  "one-time":          "bg-slate-700/50 text-slate-300 border border-slate-600",
  "daily":             "bg-accent/15 text-accent border border-accent/40",
  "weekly":            "bg-blue-500/15 text-blue-300 border border-blue-500/40",
  "monthly":           "bg-purple-500/15 text-purple-300 border border-purple-500/40",
  "frequent":          "bg-cyan-500/15 text-cyan-300 border border-cyan-500/40",
  "template:daily":    "bg-accent/25 text-accent border border-accent/60",
  "template:weekly":   "bg-blue-500/25 text-blue-300 border border-blue-500/60",
  "template:monthly":  "bg-purple-500/25 text-purple-300 border border-purple-500/60",
};

function ActionBadge({ action }: { action?: string }) {
  const a = action || "one-time";
  const cls = ACTION_BADGE[a] || ACTION_BADGE["one-time"];
  const isTpl = a.startsWith("template:");
  const label = isTpl ? `📋 ${a.split(":")[1]}` : (a === "one-time" ? "one-time" : `🔁 ${a}`);
  return <span className={`text-xs px-2 py-0.5 rounded ${cls}`}>{label}</span>;
}
type Asst = { id: number; name: string; status: string };
type Group = { id: number; name: string };

const STATUS_COLOR: Record<string, string> = {
  pending: "bg-yellow-500/10 text-yellow-300 border border-yellow-500/30",
  in_progress: "bg-blue-500/10 text-blue-300 border border-blue-500/30",
  done: "bg-green-500/10 text-green-300 border border-green-500/30",
  overdue: "bg-red-500/10 text-red-300 border border-red-500/30",
  cancelled: "bg-slate-500/10 text-slate-300 border border-slate-500/30",
  declined: "bg-red-500/10 text-red-300 border border-red-500/30",
};

const MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
type Tab = "all" | "daily" | "weekly" | "monthly";

function groupByMonthWeek(jobs: Job[]) {
  // Map<"2026-05 May", Map<"Week 2", Job[]>>
  const byMonth = new Map<string, Map<string, Job[]>>();
  for (const j of [...jobs].sort((a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime())) {
    const d = new Date(j.created_at);
    const monthKey = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")} ${MONTH_NAMES[d.getMonth()]}`;
    const weekKey = `Week ${Math.floor((d.getDate() - 1) / 7) + 1}`;
    if (!byMonth.has(monthKey)) byMonth.set(monthKey, new Map());
    const byWeek = byMonth.get(monthKey)!;
    if (!byWeek.has(weekKey)) byWeek.set(weekKey, []);
    byWeek.get(weekKey)!.push(j);
  }
  return byMonth;
}

export default function Jobs() {
  const qc = useQueryClient();
  const jobs = useQuery<Job[]>({ queryKey: ["jobs"], queryFn: async () => (await api.get("/jobs")).data });
  const templates = useQuery<Job[]>({
    queryKey: ["job-templates"],
    queryFn: async () => (await api.get("/jobs/templates")).data,
  });
  const assts = useQuery<Asst[]>({ queryKey: ["assistants"], queryFn: async () => (await api.get("/assistants")).data });
  const groupsQ = useQuery<Group[]>({ queryKey: ["groups"], queryFn: async () => (await api.get("/groups")).data });

  const [tab, setTab] = useState<Tab>("all");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    title: "", description: "", report_type: "photo", deadline_at: "",
    assistant_ids: [] as number[], group_ids: [] as number[],
    completion_mode: "all",
    accept_mode: "any",
    recurrence: "none",
    recurrence_every: 1,
  });
  const [err, setErr] = useState("");

  // ---- Edit / delete state ----
  const [editJob, setEditJob] = useState<Job | null>(null);
  const [editForm, setEditForm] = useState({
    title: "", description: "", report_type: "photo", deadline_at: "",
    assistant_ids: [] as number[], group_ids: [] as number[],
    recurrence: "none", recurrence_every: 1,
  });
  const [editErr, setEditErr] = useState("");

  function openEdit(job: Job) {
    setEditErr("");
    // Current assignees from active rows (pending/accepted/in_progress/done)
    const activeAssigneeIds = job.assignments
      .filter((a) => ["pending", "accepted", "in_progress", "done"].includes(a.status))
      .map((a) => a.assistant_id);
    // Resolve recurrence: instance → look at action; template → recurrence
    let recur = "none";
    if (job.is_template && job.recurrence) recur = job.recurrence;
    else if (job.action && job.action !== "one-time" && !job.action.startsWith("template:")) {
      recur = job.action; // "daily" | "weekly" | "monthly" | "frequent"
    }
    setEditForm({
      title: job.title,
      description: job.description || "",
      report_type: job.report_type,
      deadline_at: job.deadline_at ? new Date(job.deadline_at).toISOString().slice(0, 16) : "",
      assistant_ids: Array.from(new Set(activeAssigneeIds)),
      group_ids: [],  // we don't currently round-trip group assignment, edits are explicit ids
      recurrence: recur,
      recurrence_every: 1,
    });
    setEditJob(job);
  }

  function toggleEditAssistant(id: number) {
    setEditForm((f) => ({
      ...f,
      assistant_ids: f.assistant_ids.includes(id)
        ? f.assistant_ids.filter((x) => x !== id) : [...f.assistant_ids, id],
    }));
  }

  const update = useMutation({
    mutationFn: async () => {
      if (!editJob) return;
      return (await api.patch(`/jobs/${editJob.id}`, {
        title: editForm.title,
        description: editForm.description,
        report_type: editForm.report_type,
        deadline_at: editForm.deadline_at ? new Date(editForm.deadline_at).toISOString() : null,
        assistant_ids: editForm.assistant_ids,
        group_ids: editForm.group_ids,
        recurrence: editForm.recurrence,
        recurrence_every: editForm.recurrence_every,
      })).data;
    },
    onSuccess: () => {
      setEditJob(null); toast.success("Job updated");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["job-templates"] });
    },
    onError: (e: any) => setEditErr(e.response?.data?.detail || "Failed"),
  });

  const del = useMutation({
    mutationFn: async (id: number) => (await api.delete(`/jobs/${id}`)).data,
    onSuccess: () => {
      toast.success("Job deleted");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["job-templates"] });
    },
  });

  const create = useMutation({
    mutationFn: async () => (await api.post("/jobs", {
      ...form,
      deadline_at: form.deadline_at ? new Date(form.deadline_at).toISOString() : null,
    })).data,
    onSuccess: () => {
      setOpen(false); toast.success(form.recurrence !== "none" ? "Recurring job + first instance created" : "Job created");
      setForm({ title: "", description: "", report_type: "photo", deadline_at: "",
                assistant_ids: [], group_ids: [], completion_mode: "all", accept_mode: "any",
                recurrence: "none", recurrence_every: 1 });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["job-templates"] });
    },
    onError: (e: any) => setErr(e.response?.data?.detail || "Failed"),
  });

  function toggleAssistant(id: number) {
    setForm((f) => ({
      ...f, assistant_ids: f.assistant_ids.includes(id)
        ? f.assistant_ids.filter((x) => x !== id) : [...f.assistant_ids, id],
    }));
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <button onClick={() => setOpen(true)} className="btn-accent inline-flex items-center gap-1">
          <Plus size={16} /> Create Job
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-800 flex gap-4">
        {([
          ["all",     "All Jobs",  jobs.data?.length ?? 0],
          ["daily",   "Daily",     templates.data?.filter(t => t.recurrence === "daily").length ?? 0],
          ["weekly",  "Weekly",    templates.data?.filter(t => t.recurrence === "weekly").length ?? 0],
          ["monthly", "Monthly",   templates.data?.filter(t => t.recurrence === "monthly").length ?? 0],
        ] as [Tab, string, number][]).map(([t, label, count]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm transition flex items-center gap-1 ${
              tab === t ? "border-b-2 border-accent text-white" : "text-slate-400 hover:text-slate-200"
            }`}>
            {t !== "all" && <Repeat size={13} />}
            {label}
            <span className={`text-xs px-1.5 py-0.5 rounded ${tab === t ? "bg-accent/20" : "bg-slate-800"}`}>
              {count}
            </span>
          </button>
        ))}
      </div>

      {tab === "all" && (
        <AllJobsView
          jobs={jobs.data ?? []}
          onEdit={openEdit}
          onDelete={(id) => { if (confirm("Delete this job?")) del.mutate(id); }}
        />
      )}
      {tab !== "all" && (
        <TemplatesView
          templates={(templates.data ?? []).filter(t => t.recurrence === tab)}
          cadence={tab}
          onEdit={openEdit}
          onDelete={(id) => { if (confirm("Delete this template (and stop spawning new instances)?")) del.mutate(id); }}
        />
      )}

      {open && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-10">
          <div className="card p-6 w-[34rem] space-y-3 max-h-[90vh] overflow-auto">
            <h2 className="font-medium text-lg">New Job</h2>
            <input className="border rounded px-3 py-2 w-full" placeholder="Title"
              value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            <textarea className="border rounded px-3 py-2 w-full" placeholder="Description" rows={3}
              value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-slate-400">Report type</label>
                <select className="border rounded px-3 py-2 w-full mt-1" value={form.report_type}
                  onChange={(e) => setForm({ ...form, report_type: e.target.value })}>
                  <option value="photo">Photo</option>
                  <option value="video">Video</option>
                  <option value="document">Document</option>
                  <option value="text">Text</option>
                  <option value="any">Any</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-400">Deadline</label>
                <input type="datetime-local" className="border rounded px-3 py-2 w-full mt-1"
                  value={form.deadline_at} onChange={(e) => setForm({ ...form, deadline_at: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-slate-400">Recurrence</label>
                <select className="border rounded px-3 py-2 w-full mt-1" value={form.recurrence}
                  onChange={(e) => setForm({ ...form, recurrence: e.target.value })}>
                  <option value="none">None (one-off)</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                  <option value="frequent">Frequent (every N days)</option>
                </select>
                {form.recurrence === "frequent" && (
                  <input type="number" min={1} max={365}
                    className="border rounded px-3 py-2 w-full mt-2"
                    placeholder="Every N days"
                    value={form.recurrence_every}
                    onChange={(e) => setForm({ ...form, recurrence_every: Math.max(1, Number(e.target.value) || 1) })} />
                )}
                {form.recurrence !== "none" && (
                  <div className="text-xs text-slate-500 mt-1">
                    🔁 Template တစ်ခု + first instance တစ်ခု auto-create လုပ်မှာ
                  </div>
                )}
              </div>
              <div>
                <label className="text-xs text-slate-400">Groups</label>
                <div className="max-h-32 overflow-auto border rounded p-2 mt-1 space-y-1">
                  {groupsQ.data?.map((g) => (
                    <label key={g.id} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={form.group_ids.includes(g.id)}
                        onChange={() => setForm((f) => ({ ...f,
                          group_ids: f.group_ids.includes(g.id) ? f.group_ids.filter((x) => x !== g.id) : [...f.group_ids, g.id]
                        }))} />
                      {g.name}
                    </label>
                  ))}
                  {!groupsQ.data?.length && <div className="text-xs text-slate-500">No groups.</div>}
                </div>
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs text-slate-400">Assistants</label>
                {assts.data?.some((a) => a.status === "active") && (() => {
                  const activeIds = assts.data!.filter((a) => a.status === "active").map((a) => a.id);
                  const allSelected = activeIds.every((id) => form.assistant_ids.includes(id));
                  return (
                    <button type="button" className="text-xs text-accent hover:underline"
                      onClick={() => setForm((f) => ({
                        ...f,
                        assistant_ids: allSelected
                          ? f.assistant_ids.filter((id) => !activeIds.includes(id))
                          : Array.from(new Set([...f.assistant_ids, ...activeIds])),
                      }))}>
                      {allSelected ? "Clear all" : "Select all"}
                    </button>
                  );
                })()}
              </div>
              <div className="max-h-32 overflow-auto border rounded p-2 mt-1 space-y-1">
                {assts.data?.filter((a) => a.status === "active").map((a) => (
                  <label key={a.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={form.assistant_ids.includes(a.id)} onChange={() => toggleAssistant(a.id)} />
                    {a.name}
                  </label>
                ))}
                {!assts.data?.some((a) => a.status === "active") &&
                  <div className="text-xs text-slate-500">No active assistants.</div>}
              </div>
            </div>
            {(form.assistant_ids.length + form.group_ids.length > 1 ||
              form.group_ids.length >= 1) && (
              <div>
                <label className="text-xs text-slate-400">Accept mode</label>
                <div className="flex gap-4 mt-1 text-sm">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="acceptmode" value="any"
                      checked={form.accept_mode === "any"}
                      onChange={() => setForm({ ...form, accept_mode: "any" })} />
                    <span><b>Any</b> — တစ်ယောက် လက်ခံတာနဲ့ in_progress</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="acceptmode" value="all"
                      checked={form.accept_mode === "all"}
                      onChange={() => setForm({ ...form, accept_mode: "all" })} />
                    <span><b>All</b> — လူတိုင်း လက်ခံမှ in_progress (1h အတွင်း မလက်ခံရင် reminder)</span>
                  </label>
                </div>
              </div>
            )}
            {err && <div className="text-sm text-red-400">{err}</div>}
            <div className="flex justify-end gap-2">
              <button className="px-4 py-2 text-slate-400" onClick={() => setOpen(false)}>Cancel</button>
              <button disabled={create.isPending || !form.title || (!form.assistant_ids.length && !form.group_ids.length)}
                className="btn-accent disabled:opacity-50"
                onClick={() => { setErr(""); create.mutate(); }}>Create</button>
            </div>
          </div>
        </div>
      )}

      {/* ---------------- Edit modal ---------------- */}
      {editJob && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-20">
          <div className="card p-6 w-[36rem] space-y-3 max-h-[90vh] overflow-auto">
            <div className="flex items-center justify-between">
              <h2 className="font-medium text-lg">Edit {editJob.code}</h2>
              <span className="text-xs text-slate-500">
                {editJob.is_template ? "📋 template" : `${editJob.action}`}
              </span>
            </div>
            <input className="border rounded px-3 py-2 w-full" placeholder="Title"
              value={editForm.title} onChange={(e) => setEditForm({ ...editForm, title: e.target.value })} />
            <textarea className="border rounded px-3 py-2 w-full" placeholder="Description" rows={3}
              value={editForm.description} onChange={(e) => setEditForm({ ...editForm, description: e.target.value })} />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-slate-400">Report type</label>
                <select className="border rounded px-3 py-2 w-full mt-1" value={editForm.report_type}
                  onChange={(e) => setEditForm({ ...editForm, report_type: e.target.value })}>
                  <option value="photo">Photo</option>
                  <option value="video">Video</option>
                  <option value="document">Document</option>
                  <option value="text">Text</option>
                  <option value="any">Any</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-400">Deadline</label>
                <input type="datetime-local" className="border rounded px-3 py-2 w-full mt-1"
                  value={editForm.deadline_at} onChange={(e) => setEditForm({ ...editForm, deadline_at: e.target.value })} />
              </div>
            </div>

            <div>
              <label className="text-xs text-slate-400">Action (recurrence)</label>
              <select className="border rounded px-3 py-2 w-full mt-1" value={editForm.recurrence}
                onChange={(e) => setEditForm({ ...editForm, recurrence: e.target.value })}>
                <option value="none">One-time (no auto-spawn)</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="frequent">Frequent (every N days)</option>
              </select>
              {editForm.recurrence === "frequent" && (
                <input type="number" min={1} max={365}
                  className="border rounded px-3 py-2 w-full mt-2"
                  placeholder="Every N days"
                  value={editForm.recurrence_every}
                  onChange={(e) => setEditForm({ ...editForm, recurrence_every: Math.max(1, Number(e.target.value) || 1) })} />
              )}
              {editForm.recurrence !== "none" && !editJob.is_template && !editJob.parent_template_id && (
                <div className="text-xs text-slate-500 mt-1">
                  🔁 One-time → recurring ပြောင်းမယ် — template တစ်ခု auto-create ပြီး future spawn စတင်မယ်။
                </div>
              )}
              {editJob.parent_template_id && (
                <div className="text-xs text-slate-500 mt-1">
                  ⚠️ ဒါက recurring instance — recurrence ပြောင်းရင် parent template (#{editJob.parent_template_id}) ကို cascade ဖြင့် update ဖြစ်မယ် (future spawn အကုန် အကျိုးသက်ရောက်)
                </div>
              )}
            </div>

            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs text-slate-400">Assignees (overwrite — checked = assigned)</label>
                {assts.data?.some((a) => a.status === "active") && (() => {
                  const activeIds = assts.data!.filter((a) => a.status === "active").map((a) => a.id);
                  const allSelected = activeIds.every((id) => editForm.assistant_ids.includes(id));
                  return (
                    <button type="button" className="text-xs text-accent hover:underline"
                      onClick={() => setEditForm((f) => ({
                        ...f,
                        assistant_ids: allSelected
                          ? f.assistant_ids.filter((id) => !activeIds.includes(id))
                          : Array.from(new Set([...f.assistant_ids, ...activeIds])),
                      }))}>
                      {allSelected ? "Clear all" : "Select all"}
                    </button>
                  );
                })()}
              </div>
              <div className="max-h-32 overflow-auto border rounded p-2 mt-1 space-y-1">
                {assts.data?.filter((a) => a.status === "active").map((a) => (
                  <label key={a.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={editForm.assistant_ids.includes(a.id)}
                      onChange={() => toggleEditAssistant(a.id)} />
                    {a.name}
                  </label>
                ))}
                {!assts.data?.some((a) => a.status === "active") &&
                  <div className="text-xs text-slate-500">No active assistants.</div>}
              </div>
              <div className="text-xs text-slate-500 mt-1">
                💡 Removed staff က ipfs notification ရရှိမယ်။ Newly added staff က ✅❌➡️🏖️ buttons နဲ့ job card ရရှိမယ်။ Admin bot ကို summary ပို့ပေးမယ်။
              </div>
            </div>

            {editErr && <div className="text-sm text-red-400">{editErr}</div>}
            <div className="flex justify-end gap-2">
              <button className="px-4 py-2 text-slate-400" onClick={() => setEditJob(null)}>Cancel</button>
              <button disabled={update.isPending || !editForm.title || !editForm.assistant_ids.length}
                className="btn-accent disabled:opacity-50"
                onClick={() => { setEditErr(""); update.mutate(); }}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- All Jobs view (collapsible Month → Week) ---------------- */

type RowActions = { onEdit: (j: Job) => void; onDelete: (id: number) => void };

function AllJobsView({ jobs, onEdit, onDelete }: { jobs: Job[] } & RowActions) {
  // ⚠️ All hooks must run on every render in the same order — never gate them
  // behind early returns. The empty-state branch is rendered AFTER the hooks.
  const grouped = useMemo(() => groupByMonthWeek(jobs), [jobs]);
  const monthKeys = useMemo(() => [...grouped.keys()].sort().reverse(), [grouped]);

  const [openMonths, setOpenMonths] = useState<Record<string, boolean>>({});
  const [openWeeks, setOpenWeeks] = useState<Record<string, boolean>>({});

  // Initialise default-open sections once we know the most recent month.
  // This is a controlled effect that runs whenever the latest month key changes.
  const latestMonth = monthKeys[0];
  useMemo(() => {
    if (!latestMonth) return;
    setOpenMonths((m) => (m[latestMonth] ? m : { ...m, [latestMonth]: true }));
    const wks = grouped.get(latestMonth);
    if (!wks) return;
    setOpenWeeks((w) => {
      const out = { ...w };
      let changed = false;
      wks.forEach((_, weekKey) => {
        const k = `${latestMonth}::${weekKey}`;
        if (!out[k]) { out[k] = true; changed = true; }
      });
      return changed ? out : w;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestMonth]);

  if (!jobs.length) {
    return <div className="card p-8 text-center text-slate-500">No jobs yet</div>;
  }

  return (
    <div className="space-y-2">
      {monthKeys.map((monthKey) => {
        const byWeek = grouped.get(monthKey)!;
        const monthLabel = monthKey.split(" ").slice(1).join(" ") + " " + monthKey.split("-")[0];
        const monthOpen = openMonths[monthKey] ?? false;
        const monthTotal = [...byWeek.values()].reduce((s, l) => s + l.length, 0);
        return (
          <div key={monthKey} className="card overflow-hidden">
            <button
              className="w-full flex items-center justify-between p-3 hover:bg-slate-800/40 transition"
              onClick={() => setOpenMonths((m) => ({ ...m, [monthKey]: !monthOpen }))}>
              <div className="flex items-center gap-2">
                <ChevronRight size={16}
                  className={`transition-transform ${monthOpen ? "rotate-90" : ""}`} />
                <span className="font-medium">{monthLabel}</span>
              </div>
              <span className="text-xs text-slate-400">{monthTotal} jobs</span>
            </button>
            {monthOpen && (
              <div className="border-t border-slate-800">
                {[...byWeek.entries()]
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([weekKey, list]) => {
                    const wKey = `${monthKey}::${weekKey}`;
                    const weekOpen = openWeeks[wKey] ?? false;
                    return (
                      <div key={wKey} className="border-b border-slate-800/50 last:border-b-0">
                        <button
                          className="w-full flex items-center justify-between px-4 py-2 hover:bg-slate-800/30 text-sm"
                          onClick={() => setOpenWeeks((w) => ({ ...w, [wKey]: !weekOpen }))}>
                          <div className="flex items-center gap-2">
                            <ChevronRight size={13}
                              className={`transition-transform ${weekOpen ? "rotate-90" : ""}`} />
                            <span className="text-slate-300">{weekKey}</span>
                          </div>
                          <span className="text-xs text-slate-500">{list.length}</span>
                        </button>
                        {weekOpen && <JobTable jobs={list} onEdit={onEdit} onDelete={onDelete} />}
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function JobTable({ jobs, onEdit, onDelete }: { jobs: Job[] } & RowActions) {
  return (
    <table className="w-full text-sm">
      <thead className="bg-slate-800/30 text-left text-xs text-slate-500">
        <tr>
          <th className="p-2 pl-10">Code</th>
          <th className="p-2">Title</th>
          <th className="p-2">Action</th>
          <th className="p-2">Type</th>
          <th className="p-2">Assignees</th>
          <th className="p-2">Source</th>
          <th className="p-2">Deadline</th>
          <th className="p-2">Status</th>
          <th className="p-2 text-right pr-4">Edit</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id} className="border-t border-slate-800 hover:bg-slate-800/20">
            <td className="p-2 pl-10 font-mono text-xs">
              <Link to={`/jobs/${j.id}`} className="text-accent hover:underline">{j.code}</Link>
            </td>
            <td className="p-2 font-medium">{j.title}</td>
            <td className="p-2"><ActionBadge action={j.action} /></td>
            <td className="p-2 text-xs">{j.report_type}</td>
            <td className="p-2 text-xs">{j.assignments.map((a) => a.assistant_name).join(", ")}</td>
            <td className="p-2 text-xs">
              <span className={j.created_via === "admin_bot" ? "text-accent" : "text-slate-400"}>
                {j.created_via === "admin_bot" ? "🤖" : "🌐"}
              </span>
            </td>
            <td className="p-2 text-xs">{j.deadline_at ? format(new Date(j.deadline_at), "MM-dd HH:mm") : "—"}</td>
            <td className="p-2">
              <span className={`text-xs px-2 py-0.5 rounded ${STATUS_COLOR[j.status] || "bg-slate-700"}`}>{j.status}</span>
            </td>
            <td className="p-2 pr-4">
              <div className="flex items-center justify-end gap-2">
                <button title="Edit" onClick={() => onEdit(j)}
                  className="text-slate-300 hover:text-accent p-1 rounded hover:bg-slate-800">
                  <Pencil size={14} />
                </button>
                <button title="Delete" onClick={() => onDelete(j.id)}
                  className="text-red-400 hover:text-red-300 p-1 rounded hover:bg-red-500/10">
                  <Trash2 size={14} />
                </button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ---------------- Recurring templates view ---------------- */

function TemplatesView({ templates, cadence, onEdit, onDelete }: { templates: Job[]; cadence: Tab } & RowActions) {
  const cadenceLabel = { daily: "Daily", weekly: "Weekly", monthly: "Monthly", all: "" }[cadence];
  if (!templates.length) {
    return (
      <div className="card p-8 text-center text-slate-500 space-y-2">
        <Repeat size={28} className="mx-auto text-slate-600" />
        <div>No <b>{cadenceLabel.toLowerCase()}</b> recurring jobs yet.</div>
        <div className="text-xs">
          Create one via <span className="text-accent">+ Create Job</span> and choose Recurrence = <b>{cadenceLabel}</b>,
          or via Telegram: <code className="text-accent">/newjob title, , 9am, @shop1, {cadence}</code>
        </div>
      </div>
    );
  }
  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-800/50 text-left text-slate-400">
          <tr>
            <th className="p-3">Code</th><th className="p-3">Title</th>
            <th className="p-3">Type</th><th className="p-3">Assignees</th>
            <th className="p-3">Cadence</th>
            <th className="p-3">Next spawn</th>
            <th className="p-3">Created</th>
            <th className="p-3 text-right pr-4">Edit</th>
          </tr>
        </thead>
        <tbody>
          {templates.map((t) => (
            <tr key={t.id} className="border-t border-slate-800 hover:bg-slate-800/40">
              <td className="p-3 font-mono text-xs">
                <Link to={`/jobs/${t.id}`} className="text-accent hover:underline">{t.code}</Link>
                <span className="ml-1 text-[10px] text-slate-500">📋</span>
              </td>
              <td className="p-3 font-medium">{t.title}</td>
              <td className="p-3 text-xs">{t.report_type}</td>
              <td className="p-3 text-xs">{t.assignments.map((a) => a.assistant_name).join(", ")}</td>
              <td className="p-3 text-xs">
                <span className="px-2 py-0.5 rounded bg-accent/10 text-accent border border-accent/30">
                  🔁 {t.recurrence}
                </span>
              </td>
              <td className="p-3 text-xs text-slate-300">
                {t.next_spawn_at ? format(new Date(t.next_spawn_at), "yyyy-MM-dd HH:mm") : "—"}
              </td>
              <td className="p-3 text-xs text-slate-500">
                {format(new Date(t.created_at), "yyyy-MM-dd")}
              </td>
              <td className="p-3 pr-4">
                <div className="flex items-center justify-end gap-2">
                  <button title="Edit" onClick={() => onEdit(t)}
                    className="text-slate-300 hover:text-accent p-1 rounded hover:bg-slate-800">
                    <Pencil size={14} />
                  </button>
                  <button title="Delete" onClick={() => onDelete(t.id)}
                    className="text-red-400 hover:text-red-300 p-1 rounded hover:bg-red-500/10">
                    <Trash2 size={14} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
