import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

const ACTOR_TYPES = ["", "web_admin", "admin_bot", "assistant_bot", "system"];

export default function AuditLog() {
  const [actorType, setActorType] = useState("");
  const [action, setAction] = useState("");

  const { data, refetch, isFetching } = useQuery({
    queryKey: ["audit", actorType, action],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (actorType) params.set("actor_type", actorType);
      if (action) params.set("action", action);
      params.set("limit", "200");
      return (await api.get(`/audit?${params}`)).data;
    },
  });

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-semibold">Audit Log</h1>
      <div className="card p-4 flex gap-3 items-end">
        <div>
          <label className="text-xs text-slate-400">Actor type</label>
          <select className="border rounded px-3 py-2 mt-1" value={actorType} onChange={(e) => setActorType(e.target.value)}>
            {ACTOR_TYPES.map((t) => <option key={t} value={t}>{t || "any"}</option>)}
          </select>
        </div>
        <div className="flex-1">
          <label className="text-xs text-slate-400">Action contains</label>
          <input className="border rounded px-3 py-2 w-full mt-1"
            placeholder="e.g. job_created, blocked_command"
            value={action} onChange={(e) => setAction(e.target.value)} />
        </div>
        <button className="btn-accent" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? "Loading…" : "Apply"}
        </button>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-800/50 text-left text-slate-400">
            <tr>
              <th className="p-3">Time</th><th className="p-3">Actor</th>
              <th className="p-3">Action</th><th className="p-3">Target</th>
              <th className="p-3">Payload</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r: any) => (
              <tr key={r.id} className="border-t border-slate-800">
                <td className="p-3 text-xs font-mono text-slate-400">{new Date(r.ts).toLocaleString()}</td>
                <td className="p-3">
                  <span className="text-xs px-2 py-0.5 rounded bg-slate-800 border border-slate-700">{r.actor_type}</span>
                  {r.actor_id != null && <span className="text-xs text-slate-500 ml-1">#{r.actor_id}</span>}
                </td>
                <td className="p-3 font-medium">{r.action}</td>
                <td className="p-3 text-xs text-slate-400">
                  {r.target_type ? `${r.target_type}#${r.target_id}` : "—"}
                </td>
                <td className="p-3 text-xs text-slate-500 font-mono max-w-md truncate">
                  {r.payload ? JSON.stringify(r.payload) : ""}
                </td>
              </tr>
            ))}
            {!data?.length && <tr><td colSpan={5} className="p-6 text-center text-slate-500">No audit entries.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
