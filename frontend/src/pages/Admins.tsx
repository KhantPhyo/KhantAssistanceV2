import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Plus, Trash2, Pencil } from "lucide-react";
import toast from "react-hot-toast";
import { useAuth } from "../contexts/AuthContext";

type Admin = { id: number; email: string; role: string; telegram_username?: string; is_active: boolean; created_at: string };

export default function Admins() {
  const { role: myRole } = useAuth();
  const qc = useQueryClient();
  const list = useQuery<Admin[]>({ queryKey: ["admins"], queryFn: async () => (await api.get("/admins")).data });
  const me = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/auth/me")).data });
  const [open, setOpen] = useState<{ id?: number; email: string; password: string; role: string; telegram_username: string; is_active: boolean } | null>(null);
  const [err, setErr] = useState("");

  const isWebAdmin = myRole === "web_admin";

  const save = useMutation({
    mutationFn: async () => {
      if (!open) return;
      if (open.id) {
        const payload: any = { telegram_username: open.telegram_username || null };
        if (open.password) payload.password = open.password;
        if (isWebAdmin) { payload.role = open.role; payload.is_active = open.is_active; }
        return (await api.patch(`/admins/${open.id}`, payload)).data;
      }
      return (await api.post("/admins", {
        email: open.email, password: open.password, role: open.role,
        telegram_username: open.telegram_username || null,
      })).data;
    },
    onSuccess: () => { setOpen(null); toast.success("Saved"); qc.invalidateQueries({ queryKey: ["admins"] }); qc.invalidateQueries({ queryKey: ["me"] }); },
    onError: (e: any) => setErr(e.response?.data?.detail || "Failed"),
  });

  const del = useMutation({
    mutationFn: async (id: number) => (await api.delete(`/admins/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admins"] }),
    onError: (e: any) => toast.error(e.response?.data?.detail || "Failed"),
  });

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Admins</h1>
        {isWebAdmin && (
          <button className="btn-accent inline-flex items-center gap-1"
            onClick={() => { setErr(""); setOpen({ email: "", password: "", role: "web_admin", telegram_username: "", is_active: true }); }}>
            <Plus size={16} /> Add Admin
          </button>
        )}
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-800/50 text-left text-slate-400">
            <tr>
              <th className="p-3">Email</th><th className="p-3">Role</th>
              <th className="p-3">Telegram</th><th className="p-3">Active</th><th></th>
            </tr>
          </thead>
          <tbody>
            {list.data?.map((u) => {
              const isMe = me.data?.id === u.id;
              return (
                <tr key={u.id} className="border-t border-slate-800">
                  <td className="p-3 font-medium">{u.email} {isMe && <span className="text-xs text-accent ml-1">(you)</span>}</td>
                  <td className="p-3 text-xs">{u.role}</td>
                  <td className="p-3 text-xs">{u.telegram_username ? `@${u.telegram_username}` : "—"}</td>
                  <td className="p-3 text-xs">{u.is_active ? "✓" : "✗"}</td>
                  <td className="p-3 flex gap-2 justify-end">
                    {(isWebAdmin || isMe) && (
                      <button className="text-slate-300 hover:text-accent"
                        onClick={() => { setErr(""); setOpen({
                          id: u.id, email: u.email, password: "", role: u.role,
                          telegram_username: u.telegram_username || "", is_active: u.is_active,
                        }); }}><Pencil size={16} /></button>
                    )}
                    {isWebAdmin && !isMe && (
                      <button className="text-red-400 hover:text-red-300"
                        onClick={() => { if (confirm(`Delete ${u.email}?`)) del.mutate(u.id); }}>
                        <Trash2 size={16} />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {open && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-10">
          <div className="card p-6 w-[28rem] space-y-3">
            <h2 className="font-medium text-lg">{open.id ? "Edit Admin" : "New Admin"}</h2>
            {!open.id && (
              <input className="border rounded px-3 py-2 w-full" placeholder="Email"
                value={open.email} onChange={(e) => setOpen({ ...open, email: e.target.value })} />
            )}
            <input type="password" className="border rounded px-3 py-2 w-full"
              placeholder={open.id ? "New password (optional)" : "Password"}
              value={open.password} onChange={(e) => setOpen({ ...open, password: e.target.value })} />
            <input className="border rounded px-3 py-2 w-full" placeholder="Telegram @username (no @)"
              value={open.telegram_username} onChange={(e) => setOpen({ ...open, telegram_username: e.target.value })} />
            {isWebAdmin && (
              <>
                <select className="border rounded px-3 py-2 w-full" value={open.role}
                  onChange={(e) => setOpen({ ...open, role: e.target.value })}>
                  <option value="web_admin">web_admin (full access)</option>
                  <option value="remote_admin">remote_admin (bot-only)</option>
                </select>
                {open.id && (
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={open.is_active}
                      onChange={(e) => setOpen({ ...open, is_active: e.target.checked })} />
                    Active
                  </label>
                )}
              </>
            )}
            {err && <div className="text-sm text-red-400">{err}</div>}
            <div className="flex justify-end gap-2">
              <button className="px-4 py-2 text-slate-400" onClick={() => setOpen(null)}>Cancel</button>
              <button disabled={save.isPending || (!open.id && (!open.email || !open.password))}
                className="btn-accent disabled:opacity-50"
                onClick={() => { setErr(""); save.mutate(); }}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
