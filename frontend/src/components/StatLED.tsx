import clsx from "clsx";

const COLOR: Record<string, string> = {
  active: "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.7)]",
  pending: "bg-yellow-500",
  paused: "bg-orange-500",
  removed: "bg-slate-500",
  revoked: "bg-red-500",
};

export function StatLED({ status, label }: { status: string; label?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={clsx("w-2.5 h-2.5 rounded-full", COLOR[status] || "bg-slate-400")} />
      {label !== false && <span className="text-xs text-slate-300">{status}</span>}
    </span>
  );
}
