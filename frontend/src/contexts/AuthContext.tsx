import { createContext, useContext, useState, ReactNode } from "react";
import { api } from "../api/client";

type AuthCtx = {
  token: string | null;
  email: string | null;
  role: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const Ctx = createContext<AuthCtx>(null as any);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(localStorage.getItem("token"));
  const [email, setEmail] = useState<string | null>(localStorage.getItem("email"));
  const [role, setRole] = useState<string | null>(localStorage.getItem("role"));

  async function login(e: string, p: string) {
    const { data } = await api.post("/auth/login", { email: e, password: p });
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("email", data.email);
    localStorage.setItem("role", data.role);
    setToken(data.access_token); setEmail(data.email); setRole(data.role);
  }
  function logout() {
    localStorage.removeItem("token");
    localStorage.removeItem("email");
    localStorage.removeItem("role");
    setToken(null); setEmail(null); setRole(null);
  }
  return <Ctx.Provider value={{ token, email, role, login, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
