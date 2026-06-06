import { StatLED } from "./StatLED";

export function AssistantBadge({ name, status, username }: { name: string; status: string; username?: string }) {
  return (
    <div className="flex items-center justify-between bg-slate-900 border border-slate-800 rounded-lg p-3">
      <div>
        <div className="font-medium text-slate-100">{name}</div>
        {username && <div className="text-xs text-slate-400">@{username}</div>}
      </div>
      <StatLED status={status} />
    </div>
  );
}
