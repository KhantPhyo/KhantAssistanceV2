import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Trash2, Plus } from "lucide-react";
import toast from "react-hot-toast";
import { StatLED } from "../components/StatLED";

type Asst = {
  id: number; name: string; phone?: string; position?: string;
  telegram_username?: string; status: string; chat_id?: string; bot_username?: string;
};

export default function Assistants() {
  const qc = useQueryClient();
  const { data } = useQuery<Asst[]>({
    queryKey: ["assistants"],
    queryFn: async () => (await api.get("/assistants")).data,
  });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", phone: "", position: "", telegram_username: "", bot_token: "" });
  const [err, setErr] = useState("");

  const create = useMutation({
    mutationFn: async () => (await api.post("/assistants", form)).data,
    onSuccess: () => {
      setOpen(false); toast.success("Assistant created — open Telegram bot link & press Accept");
      setForm({ name: "", phone: "", position: "", telegram_username: "", bot_token: "" });
      qc.invalidateQueries({ queryKey: ["assistants"] });
    },
    onError: (e: any) => setErr(e.response?.data?.detail || "Failed"),
  });

  const del = useMutation({
    mutationFn: async (id: number) => (await api.delete(`/assistants/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assistants"] }),
  });

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Assistants</h1>
        <button onClick={() => setOpen(true)} className="btn-accent inline-flex items-center gap-1">
          <Plus size={16} /> Add Assistant
        </button>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-800/50 text-left text-slate-400">
            <tr>
              <th className="p-3">Status</th><th className="p-3">Name</th>
              <th className="p-3">@username</th><th className="p-3">Phone</th>
              <th className="p-3">Position</th><th className="p-3">Bot</th><th></th>
            </tr>
          </thead>
          <tbody>
            {data?.map((a) => (
              <tr key={a.id} className="border-t border-slate-800">
                <td className="p-3"><StatLED status={a.status} /></td>
                <td className="p-3 font-medium">{a.name}</td>
                <td className="p-3 text-xs text-slate-400">{a.telegram_username ? `@${a.telegram_username}` : "—"}</td>
                <td className="p-3">{a.phone || "—"}</td>
                <td className="p-3">{a.position || "—"}</td>
                <td className="p-3">{a.bot_username ? `@${a.bot_username}` : "—"}</td>
                <td className="p-3 text-right">
                  <button onClick={() => { if (confirm(`Delete ${a.name}?`)) del.mutate(a.id); }}
                    className="text-red-400 hover:text-red-300"><Trash2 size={16} /></button>
                </td>
              </tr>
            ))}
            {!data?.length && <tr><td colSpan={7} className="p-6 text-center text-slate-500">No assistants yet</td></tr>}
          </tbody>
        </table>
      </div>

      {open && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-10">
          <div className="card p-6 w-[28rem] space-y-3">
            <h2 className="font-medium text-lg">New Assistant</h2>
            <input className="border rounded px-3 py-2 w-full" placeholder="Name"
              value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className="border rounded px-3 py-2 w-full" placeholder="Telegram @username (no @)"
              value={form.telegram_username} onChange={(e) => setForm({ ...form, telegram_username: e.target.value })} />
            <input className="border rounded px-3 py-2 w-full" placeholder="Phone"
              value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            <input className="border rounded px-3 py-2 w-full" placeholder="Position"
              value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} />
            <input className="border rounded px-3 py-2 w-full" placeholder="Bot token (from @BotFather)"
              value={form.bot_token} onChange={(e) => setForm({ ...form, bot_token: e.target.value })} />
            {err && <div className="text-sm text-red-400">{err}</div>}
            <div className="text-xs text-slate-400">
              After creating, open the bot in Telegram and press <b>Accept</b> to bind.
              The @username is used by the admin bot for /reassign and /newjob.
            </div>
            <div className="flex justify-end gap-2">
              <button className="px-4 py-2 text-slate-400" onClick={() => setOpen(false)}>Cancel</button>
              <button disabled={create.isPending || !form.name || !form.bot_token}
                className="btn-accent disabled:opacity-50"
                onClick={() => { setErr(""); create.mutate(); }}>Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
