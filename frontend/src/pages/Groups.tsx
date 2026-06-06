import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Plus, Pencil, Trash2, X } from "lucide-react";

type Group = { id: number; name: string; description: string; assistant_ids: number[]; assistant_names: string[] };
type Asst = { id: number; name: string; status: string };

export default function Groups() {
  const qc = useQueryClient();
  const groups = useQuery<Group[]>({ queryKey: ["groups"], queryFn: async () => (await api.get("/groups")).data });
  const assts = useQuery<Asst[]>({ queryKey: ["assistants"], queryFn: async () => (await api.get("/assistants")).data });

  const [open, setOpen] = useState<{ id?: number; name: string; description: string; assistant_ids: number[] } | null>(null);
  const [err, setErr] = useState("");

  const save = useMutation({
    mutationFn: async () => {
      if (!open) return;
      const payload = { name: open.name, description: open.description, assistant_ids: open.assistant_ids };
      if (open.id) return (await api.patch(`/groups/${open.id}`, payload)).data;
      return (await api.post("/groups", payload)).data;
    },
    onSuccess: () => { setOpen(null); qc.invalidateQueries({ queryKey: ["groups"] }); },
    onError: (e: any) => setErr(e.response?.data?.detail || "Failed"),
  });

  const del = useMutation({
    mutationFn: async (id: number) => (await api.delete(`/groups/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
  });

  function toggle(id: number) {
    if (!open) return;
    setOpen({ ...open, assistant_ids: open.assistant_ids.includes(id)
      ? open.assistant_ids.filter((x) => x !== id) : [...open.assistant_ids, id] });
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Groups</h1>
        <button onClick={() => { setErr(""); setOpen({ name: "", description: "", assistant_ids: [] }); }}
          className="btn-accent inline-flex items-center gap-1">
          <Plus size={16} /> New Group
        </button>
      </div>
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-800/50 text-left text-slate-400">
            <tr><th className="p-3">Name</th><th className="p-3">Description</th><th className="p-3">Members</th><th className="w-24"></th></tr>
          </thead>
          <tbody>
            {groups.data?.map((g) => (
              <tr key={g.id} className="border-t border-slate-800 hover:bg-slate-800/40">
                <td className="p-3 font-medium">{g.name}</td>
                <td className="p-3 text-xs text-slate-400">{g.description || "—"}</td>
                <td className="p-3 text-xs">{g.assistant_names.join(", ") || <span className="text-slate-500">—</span>}</td>
                <td className="p-3 flex gap-2">
                  <button onClick={() => { setErr(""); setOpen({ id: g.id, name: g.name, description: g.description, assistant_ids: g.assistant_ids }); }}
                    className="text-slate-300 hover:text-accent"><Pencil size={16} /></button>
                  <button onClick={() => { if (confirm(`Delete ${g.name}?`)) del.mutate(g.id); }}
                    className="text-red-400 hover:text-red-300"><Trash2 size={16} /></button>
                </td>
              </tr>
            ))}
            {!groups.data?.length && <tr><td colSpan={4} className="p-6 text-center text-slate-500">No groups yet</td></tr>}
          </tbody>
        </table>
      </div>

      {open && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-10">
          <div className="card p-6 w-[32rem] space-y-3">
            <div className="flex justify-between items-center">
              <h2 className="font-medium text-lg">{open.id ? "Edit Group" : "New Group"}</h2>
              <button onClick={() => setOpen(null)}><X size={18} /></button>
            </div>
            <input className="border rounded px-3 py-2 w-full" placeholder="Group name"
              value={open.name} onChange={(e) => setOpen({ ...open, name: e.target.value })} />
            <input className="border rounded px-3 py-2 w-full" placeholder="Description (optional)"
              value={open.description} onChange={(e) => setOpen({ ...open, description: e.target.value })} />
            <div>
              <label className="text-xs text-slate-400">Members</label>
              <div className="max-h-48 overflow-auto border rounded p-2 mt-1 space-y-1">
                {assts.data?.filter((a) => a.status === "active").map((a) => (
                  <label key={a.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={open.assistant_ids.includes(a.id)} onChange={() => toggle(a.id)} />
                    {a.name}
                  </label>
                ))}
                {!assts.data?.some((a) => a.status === "active") &&
                  <div className="text-xs text-slate-500">No active assistants yet.</div>}
              </div>
            </div>
            {err && <div className="text-sm text-red-400">{err}</div>}
            <div className="flex justify-end gap-2">
              <button className="px-4 py-2 text-slate-400" onClick={() => setOpen(null)}>Cancel</button>
              <button disabled={save.isPending || !open.name.trim()}
                className="btn-accent disabled:opacity-50"
                onClick={() => { setErr(""); save.mutate(); }}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
