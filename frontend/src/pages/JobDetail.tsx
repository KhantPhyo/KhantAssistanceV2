import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { format } from "date-fns";
import { ArrowLeft, Download } from "lucide-react";

export default function JobDetail() {
  const { id } = useParams();
  const { data: job, isLoading } = useQuery({
    queryKey: ["job", id],
    queryFn: async () => (await api.get(`/jobs/${id}`)).data,
    enabled: !!id,
  });

  if (isLoading) return <div className="p-6 text-slate-400">Loading…</div>;
  if (!job) return <div className="p-6 text-slate-400">Not found.</div>;

  const tok = localStorage.getItem("token") || "";
  const dl = (rid: number) => `/api/jobs/${job.id}/reports/${rid}/download?token=${encodeURIComponent(tok)}`;
  const pv = (rid: number) => `/api/jobs/${job.id}/reports/${rid}/preview?token=${encodeURIComponent(tok)}`;

  return (
    <div className="p-6 space-y-4 max-w-4xl">
      <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
        <ArrowLeft size={14} /> Back to jobs
      </Link>
      <div className="card p-6 space-y-2">
        <div className="text-xs text-slate-500 font-mono">{job.code}</div>
        <h1 className="text-2xl font-semibold">{job.title}</h1>
        <div className="text-sm text-slate-300 whitespace-pre-wrap">{job.description}</div>
        <div className="text-xs text-slate-400 pt-2">
          Type: <b>{job.report_type}</b> · Status: <b>{job.status}</b>
          {job.deadline_at && <> · Deadline: {format(new Date(job.deadline_at), "yyyy-MM-dd HH:mm")}</>}
          {job.completed_at && <> · Completed: {format(new Date(job.completed_at), "yyyy-MM-dd HH:mm")}</>}
          {" · "}Source: <b className={job.created_via === "admin_bot" ? "text-accent" : ""}>{job.created_via}</b>
        </div>
      </div>

      <div className="card p-6">
        <h2 className="font-medium mb-3">Timeline / Assignees</h2>
        <div className="space-y-2">
          {job.assignments.map((a: any) => (
            <div key={a.id} className="flex justify-between items-center bg-slate-800/40 rounded px-3 py-2 text-sm">
              <span>{a.assistant_name}</span>
              <span className="text-xs">
                {a.status}
                {a.accepted_at && ` · accepted ${format(new Date(a.accepted_at), "MM-dd HH:mm")}`}
                {a.finished_at && ` · finished ${format(new Date(a.finished_at), "MM-dd HH:mm")}`}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="card p-6">
        <h2 className="font-medium mb-3">Reports</h2>
        {!job.reports.length && <div className="text-sm text-slate-500">No reports yet.</div>}
        <div className="space-y-3">
          {job.reports.map((r: any) => (
            <div key={r.id} className="bg-slate-800/40 rounded p-3 text-sm">
              <div className="flex justify-between items-center mb-2">
                <div>
                  <div className="font-medium">{r.file_name || r.type}</div>
                  <div className="text-xs text-slate-500">
                    {format(new Date(r.submitted_at), "yyyy-MM-dd HH:mm")}
                  </div>
                </div>
                {r.type !== "text" && (
                  <a href={dl(r.id)} className="text-slate-300 hover:text-accent inline-flex items-center gap-1">
                    <Download size={14} /> download
                  </a>
                )}
              </div>
              {r.type === "photo" && <img src={pv(r.id)} className="max-h-80 rounded" />}
              {r.type === "video" && <video src={pv(r.id)} controls className="max-h-80 rounded" />}
              {r.type === "document" && (
                <a href={pv(r.id)} target="_blank" rel="noreferrer" className="text-accent text-xs">Open document</a>
              )}
              {r.type === "text" && <pre className="whitespace-pre-wrap text-slate-200">{r.content_text}</pre>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
