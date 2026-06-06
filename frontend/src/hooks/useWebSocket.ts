import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";

export function useAppWebSocket() {
  const qc = useQueryClient();
  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) return;
    let ws: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let pingTimer: number | undefined;
    let stop = false;

    function connect() {
      if (stop) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${location.host}/ws?token=${token}`;
      ws = new WebSocket(url);
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.event?.startsWith("job")) {
            qc.invalidateQueries({ queryKey: ["jobs"] });
            qc.invalidateQueries({ queryKey: ["dashboard"] });
            if (msg.event === "job.created" && msg.data?.code) toast.success(`New job ${msg.data.code}`);
            if (msg.event === "job.status_changed") qc.invalidateQueries({ queryKey: ["job", msg.data?.job_id] });
          }
          if (msg.event?.startsWith("binding")) {
            qc.invalidateQueries({ queryKey: ["assistants"] });
            qc.invalidateQueries({ queryKey: ["admin-bots"] });
          }
          if (msg.event === "report.uploaded") {
            qc.invalidateQueries({ queryKey: ["jobs"] });
          }
        } catch {}
      };
      ws.onclose = () => {
        if (stop) return;
        reconnectTimer = window.setTimeout(connect, 1500);
      };
      ws.onerror = () => { try { ws?.close(); } catch {} };
    }

    connect();
    pingTimer = window.setInterval(() => {
      try { ws?.send("ping"); } catch {}
    }, 25000);

    return () => {
      stop = true;
      if (pingTimer) clearInterval(pingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try { ws?.close(); } catch {}
    };
  }, [qc]);
}
