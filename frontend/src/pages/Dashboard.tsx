import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid,
} from "recharts";

const COLORS = ["#22c55e", "#3b82f6", "#eab308", "#ef4444"];

export default function Dashboard() {
  const [range, setRange] = useState<"daily" | "weekly" | "monthly">("daily");
  const { data } = useQuery({
    queryKey: ["dashboard", range],
    queryFn: async () => (await api.get(`/dashboard/stats?range=${range}`)).data,
  });

  const pie = data ? Object.entries(data.status_breakdown).map(([name, value]) => ({ name, value })) : [];

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <div className="inline-flex rounded card overflow-hidden">
          {(["daily", "weekly", "monthly"] as const).map((r) => (
            <button key={r} onClick={() => setRange(r)}
              className={`px-4 py-2 text-sm transition ${range === r ? "bg-accent text-white" : "hover:bg-slate-800 text-slate-300"}`}>
              {r}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-5 gap-3">
        {[
          { k: "total", label: "Total" },
          { k: "done", label: "Done", color: "text-green-400" },
          { k: "in_progress", label: "In Progress", color: "text-blue-400" },
          { k: "pending", label: "Pending", color: "text-yellow-400" },
          { k: "overdue", label: "Overdue", color: "text-red-400" },
        ].map((s) => (
          <div key={s.k} className="card p-4">
            <div className="text-xs text-slate-500">{s.label}</div>
            <div className={`text-3xl font-semibold mt-1 ${(s as any).color || "text-slate-100"}`}>
              {data?.[s.k] ?? "—"}
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2 card p-4">
          <h3 className="font-medium mb-2 text-slate-200">Jobs over time</h3>
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <LineChart data={data?.timeseries || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="ts" tickFormatter={(v) => v.slice(5, 16)} fontSize={10} stroke="#64748b" />
                <YAxis allowDecimals={false} stroke="#64748b" />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b" }} />
                <Legend />
                <Line type="monotone" dataKey="created" stroke="#6366f1" name="Created" />
                <Line type="monotone" dataKey="done" stroke="#22c55e" name="Done" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card p-4">
          <h3 className="font-medium mb-2 text-slate-200">Status breakdown</h3>
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie data={pie} dataKey="value" nameKey="name" outerRadius={80} label>
                  {pie.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b" }} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="text-xs text-slate-400 mt-2">
            On-time ratio: {((data?.on_time_ratio ?? 0) * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      <div className="card p-4">
        <h3 className="font-medium mb-2 text-slate-200">Per-assistant</h3>
        <div style={{ height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={data?.per_assistant || []}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="name" stroke="#64748b" />
              <YAxis allowDecimals={false} stroke="#64748b" />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b" }} />
              <Legend />
              <Bar dataKey="done" stackId="a" fill="#22c55e" />
              <Bar dataKey="in_progress" stackId="a" fill="#3b82f6" />
              <Bar dataKey="declined" stackId="a" fill="#ef4444" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
