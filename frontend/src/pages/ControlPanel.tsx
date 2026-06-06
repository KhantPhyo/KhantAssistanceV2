import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { StatLED } from "../components/StatLED";

type Tab = "admin-bot" | "settings" | "allowlist" | "audit" | "notion";

const TAB_LABEL: Record<Tab, string> = {
  "admin-bot": "Admin Bot",
  settings: "Settings",
  allowlist: "Allowlist",
  audit: "Audit Log",
  notion: "Notion Sync",
};

export default function ControlPanel() {
  const [tab, setTab] = useState<Tab>("admin-bot");

  return (
    <div className="p-6 space-y-4 max-w-4xl">
      <h1 className="text-2xl font-semibold">Control Panel</h1>
      <div className="border-b border-slate-800 flex gap-4">
        {(Object.keys(TAB_LABEL) as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm transition ${
              tab === t ? "border-b-2 border-accent text-white" : "text-slate-400 hover:text-slate-200"
            }`}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>
      {tab === "admin-bot" && <AdminBotTab />}
      {tab === "settings" && <SettingsTab />}
      {tab === "allowlist" && <AllowlistTab />}
      {tab === "audit" && <AuditPreviewTab />}
      {tab === "notion" && <NotionTab />}
    </div>
  );
}

function AdminBotTab() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["admin-bots"],
    queryFn: async () => (await api.get("/admin-bots")).data,
  });
  const me = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/auth/me")).data });
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState("");

  const bind = useMutation({
    mutationFn: async (t: string) => (await api.post("/admin-bots/bind", { bot_token: t })).data,
    onSuccess: (d) => {
      setMsg(d.instructions || "Admin bot bound.");
      setToken("");
      toast.success("Admin bot bound");
      qc.invalidateQueries({ queryKey: ["admin-bots"] });
    },
    onError: (e: any) => setMsg(e.response?.data?.detail || "Failed"),
  });

  const remove = useMutation({
    mutationFn: async (id: number) => (await api.delete(`/admin-bots/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-bots"] }),
  });

  const tgWarning = me.data && !me.data.telegram_username;

  return (
    <div className="space-y-4">
      <div className="card p-5 space-y-3">
        <h2 className="font-medium">Bind your remote-control bot</h2>
        {tgWarning && (
          <div className="text-sm bg-yellow-500/10 border border-yellow-500/30 text-yellow-300 p-3 rounded">
            ⚠️ Your <b>telegram_username</b> is not set. Open <Link to="/admins" className="underline">Admins</Link> and edit
            yourself first — pairing requires it for anti-hijack verification.
          </div>
        )}
        <div className="flex gap-2">
          <input className="border rounded px-3 py-2 flex-1" placeholder="Bot token from @BotFather"
            value={token} onChange={(e) => setToken(e.target.value)} />
          <button disabled={!token || bind.isPending}
            className="btn-accent disabled:opacity-50" onClick={() => bind.mutate(token)}>Bind</button>
        </div>
        {msg && <div className="text-sm text-slate-300">{msg}</div>}
      </div>

      <div className="card p-5 space-y-3">
        <h2 className="font-medium">Active admin bots</h2>
        {!list.data?.length && <div className="text-sm text-slate-500">No admin bot bound yet.</div>}
        {list.data?.map((b: any) => (
          <div key={b.id} className="flex items-center justify-between bg-slate-800/40 rounded p-3 text-sm">
            <div>
              <div className="font-medium">{b.username ? `@${b.username}` : "(unknown)"}</div>
              <div className="text-xs text-slate-400">owner_user_id: {b.owner_user_id ?? "—"} · chat: {b.chat_id ?? "unbound"}</div>
            </div>
            <div className="flex items-center gap-3">
              <StatLED status={b.status} />
              <button className="text-red-400 hover:text-red-300 text-xs"
                onClick={() => { if (confirm("Unbind admin bot?")) remove.mutate(b.id); }}>Unbind</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SettingsTab() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: async () => (await api.get("/control/settings")).data });
  const [reminderMin, setReminderMin] = useState("");
  const [tz, setTz] = useState("");
  const [frozen, setFrozen] = useState("0");

  useEffect(() => {
    if (settings.data) {
      setReminderMin(settings.data.reminder_minutes || "15");
      setTz(settings.data.timezone || "Asia/Yangon");
      setFrozen(settings.data.bots_frozen || "0");
    }
  }, [settings.data]);

  const save = useMutation({
    mutationFn: async (v: { key: string; value: string }) => (await api.post("/control/settings", v)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); toast.success("Saved"); },
  });

  return (
    <div className="card p-5 space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-slate-400">Reminder minutes before deadline</label>
          <div className="flex gap-2 mt-1">
            <input className="border rounded px-3 py-2 w-full" value={reminderMin}
              onChange={(e) => setReminderMin(e.target.value)} />
            <button className="btn-accent"
              onClick={() => save.mutate({ key: "reminder_minutes", value: reminderMin })}>Save</button>
          </div>
        </div>
        <div>
          <label className="text-xs text-slate-400">Timezone</label>
          <div className="flex gap-2 mt-1">
            <input className="border rounded px-3 py-2 w-full" value={tz} onChange={(e) => setTz(e.target.value)} />
            <button className="btn-accent"
              onClick={() => save.mutate({ key: "timezone", value: tz })}>Save</button>
          </div>
        </div>
      </div>
      <div>
        <label className="text-xs text-slate-400">Bots frozen (refuse all bot commands)</label>
        <div className="flex gap-2 mt-1 items-center">
          <select className="border rounded px-3 py-2" value={frozen}
            onChange={(e) => setFrozen(e.target.value)}>
            <option value="0">Active</option>
            <option value="1">Paused</option>
          </select>
          <button className="btn-accent"
            onClick={() => save.mutate({ key: "bots_frozen", value: frozen })}>Save</button>
        </div>
      </div>
    </div>
  );
}

function AllowlistTab() {
  const BLOCKED = ["delete_admin", "remove_admin", "drop_admin", "drop_db", "rotate_secret", "wipe_uploads"];
  return (
    <div className="card p-5 space-y-3">
      <h2 className="font-medium">Server-side blocked commands</h2>
      <p className="text-sm text-slate-400">
        These commands are refused by the admin bot regardless of caller. They are hard-coded into
        <code className="text-accent mx-1">services/admin_bot.py</code> as <code className="text-accent">BLOCKED_COMMANDS</code>.
      </p>
      <div className="flex flex-wrap gap-2">
        {BLOCKED.map((c) => (
          <span key={c} className="text-xs px-2 py-1 rounded bg-red-500/10 text-red-300 border border-red-500/30 font-mono">
            /{c}
          </span>
        ))}
      </div>
      <p className="text-xs text-slate-500">
        Each blocked attempt writes an audit-log row with <code className="text-accent">action=blocked_command</code>.
      </p>
    </div>
  );
}

function NotionTab() {
  const status = useQuery({
    queryKey: ["notion-status"],
    queryFn: async () => (await api.get("/notion/status")).data,
    refetchInterval: 5000,
  });
  const syncAll = useMutation({
    mutationFn: async () => (await api.post("/notion/sync-all")).data,
    onSuccess: (d) => {
      if (d.ok) toast.success(`Synced ${d.synced}/${d.total} jobs (failed: ${d.failed})`);
      else toast.error(d.reason || "Sync failed");
      status.refetch();
    },
    onError: (e: any) => toast.error(e.response?.data?.detail || "Sync failed"),
  });

  const enabled = status.data?.enabled;

  return (
    <div className="space-y-4">
      <div className="card p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-medium">Notion realtime sync</h2>
          <span className={`text-xs px-2 py-1 rounded border ${
            enabled
              ? "bg-green-500/10 text-green-300 border-green-500/30"
              : "bg-slate-700/40 text-slate-300 border-slate-600/50"
          }`}>
            {enabled ? "● connected" : "○ not configured"}
          </span>
        </div>
        {!enabled && (
          <div className="text-sm bg-yellow-500/10 border border-yellow-500/30 text-yellow-300 p-3 rounded space-y-2">
            <div className="font-medium">Setup steps</div>
            <ol className="list-decimal list-inside text-xs space-y-1">
              <li>
                Create an internal integration at{" "}
                <a href="https://www.notion.so/my-integrations" target="_blank" rel="noreferrer"
                   className="underline">notion.so/my-integrations</a> → copy the secret token.
              </li>
              <li>Open the <b>Miin Admin - Report from Staff</b> page → Share → invite that integration as Editor.</li>
              <li>
                Edit <code className="text-accent">backend/.env</code> and set <code className="text-accent">NOTION_TOKEN=secret_xxx</code>.
                <br />The database id <code className="text-accent">9298ed2e0fb841a1b72d0c0602a3d995</code> is preset.
              </li>
              <li>Restart the backend (Ctrl+C → <code>./start.sh</code>).</li>
            </ol>
          </div>
        )}
        <div className="grid grid-cols-3 gap-3 text-sm">
          <div>
            <div className="text-xs text-slate-500">Total jobs</div>
            <div className="text-2xl font-semibold">{status.data?.total_jobs ?? "—"}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Synced</div>
            <div className="text-2xl font-semibold text-green-400">{status.data?.synced_jobs ?? "—"}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Pending</div>
            <div className="text-2xl font-semibold text-yellow-400">{status.data?.unsynced_jobs ?? "—"}</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            disabled={!enabled || syncAll.isPending}
            onClick={() => syncAll.mutate()}
            className="btn-accent disabled:opacity-50">
            {syncAll.isPending ? "Syncing…" : "Re-sync all jobs"}
          </button>
          {enabled && status.data?.database_id && (
            <a className="text-xs text-accent hover:underline"
               href={`https://www.notion.so/${status.data.database_id.replace(/-/g, "")}`}
               target="_blank" rel="noreferrer">
              Open Notion database →
            </a>
          )}
        </div>
        <p className="text-xs text-slate-500">
          Every job lifecycle event (created, accepted, finished, reassigned, cancelled, declined) auto-pushes
          to the Notion database. Filter views per <b>Week 1/2/3/4/5</b> in Notion's UI to mirror your existing layout.
        </p>
      </div>
    </div>
  );
}

function AuditPreviewTab() {
  const { data } = useQuery({
    queryKey: ["audit-preview"],
    queryFn: async () => (await api.get("/audit?limit=20")).data,
  });
  return (
    <div className="card p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">Recent audit entries (latest 20)</h2>
        <Link to="/audit" className="text-xs text-accent hover:underline">View full log →</Link>
      </div>
      <div className="space-y-1 text-sm">
        {data?.map((r: any) => (
          <div key={r.id} className="flex gap-3 text-xs border-b border-slate-800 py-1">
            <span className="text-slate-500 font-mono">{new Date(r.ts).toLocaleString()}</span>
            <span className="text-accent">{r.actor_type}</span>
            <span className="font-medium">{r.action}</span>
            {r.target_type && <span className="text-slate-400">{r.target_type}#{r.target_id}</span>}
          </div>
        ))}
        {!data?.length && <div className="text-slate-500">No audit entries yet.</div>}
      </div>
    </div>
  );
}
