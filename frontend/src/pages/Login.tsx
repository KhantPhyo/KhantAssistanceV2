import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("khantphyo.myanmar@gmail.com");
  const [password, setPassword] = useState("Cisco@123");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setLoading(true);
    try { await login(email, password); nav("/"); }
    catch (e: any) { setErr(e.response?.data?.detail || "Login failed"); }
    finally { setLoading(false); }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950">
      <form onSubmit={submit} className="card p-8 w-96 space-y-4">
        <div>
          <h1 className="text-xl font-semibold">Khant Assistance <span className="text-accent">v2</span></h1>
          <p className="text-xs text-slate-500 mt-1">Sign in to continue</p>
        </div>
        <div>
          <label className="text-xs text-slate-400">Email</label>
          <input className="w-full border rounded px-3 py-2 mt-1" value={email}
            onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="text-xs text-slate-400">Password</label>
          <input type="password" className="w-full border rounded px-3 py-2 mt-1"
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {err && <div className="text-sm text-red-400">{err}</div>}
        <button disabled={loading} className="btn-accent w-full disabled:opacity-50">
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
