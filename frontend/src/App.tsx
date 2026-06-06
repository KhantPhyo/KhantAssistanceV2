import { Routes, Route, Navigate, Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./contexts/AuthContext";
import { useAppWebSocket } from "./hooks/useWebSocket";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import ControlPanel from "./pages/ControlPanel";
import Assistants from "./pages/Assistants";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import Groups from "./pages/Groups";
import Admins from "./pages/Admins";
import AuditLog from "./pages/AuditLog";
import {
  LayoutDashboard, Settings, Users, ClipboardList, LogOut, Users2, ShieldCheck, ScrollText,
} from "lucide-react";

function Shell({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  const nav = useNavigate();
  const { email, role, logout } = useAuth();
  useAppWebSocket();
  const items = [
    { to: "/", label: "Dashboard", icon: LayoutDashboard },
    { to: "/jobs", label: "Jobs", icon: ClipboardList },
    { to: "/assistants", label: "Assistants", icon: Users },
    { to: "/groups", label: "Groups", icon: Users2 },
    { to: "/control", label: "Control Panel", icon: Settings },
    { to: "/admins", label: "Admins", icon: ShieldCheck },
    { to: "/audit", label: "Audit Log", icon: ScrollText },
  ];
  return (
    <div className="min-h-screen flex bg-slate-950">
      <aside className="w-60 bg-slate-900 border-r border-slate-800 text-slate-100 flex flex-col">
        <div className="p-5 text-lg font-semibold border-b border-slate-800">
          Khant Assistance <span className="text-xs text-accent ml-1">v2</span>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {items.map((it) => {
            const active = loc.pathname === it.to || (it.to !== "/" && loc.pathname.startsWith(it.to));
            const Icon = it.icon;
            return (
              <Link key={it.to} to={it.to}
                className={`flex items-center gap-2 rounded px-3 py-2 text-sm transition ${
                  active ? "bg-accent text-white" : "hover:bg-slate-800 text-slate-300"
                }`}>
                <Icon size={16} /> {it.label}
              </Link>
            );
          })}
        </nav>
        <div className="p-3 border-t border-slate-800 text-xs">
          <div className="truncate text-slate-300">{email}</div>
          <div className="text-slate-500 mb-2">{role}</div>
          <button onClick={() => { logout(); nav("/login"); }}
            className="inline-flex items-center gap-1 text-slate-400 hover:text-white">
            <LogOut size={12} /> Logout
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}

function Guard({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Guard><Dashboard /></Guard>} />
      <Route path="/jobs" element={<Guard><Jobs /></Guard>} />
      <Route path="/jobs/:id" element={<Guard><JobDetail /></Guard>} />
      <Route path="/assistants" element={<Guard><Assistants /></Guard>} />
      <Route path="/groups" element={<Guard><Groups /></Guard>} />
      <Route path="/control" element={<Guard><ControlPanel /></Guard>} />
      <Route path="/admins" element={<Guard><Admins /></Guard>} />
      <Route path="/audit" element={<Guard><AuditLog /></Guard>} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  );
}
