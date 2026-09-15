import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, clearToken, getToken, setToken } from "./api";

export interface CurrentUser {
  user_id: number;
  phone: string;
  nickname: string;
  role: "customer" | "operator" | "admin";
  balance_cents: number;
}

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  login: (phone: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState>(null as unknown as AuthState);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api
      .get<CurrentUser>("/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));

    const onUnauthorized = () => setUser(null);
    window.addEventListener("chargeflow:unauthorized", onUnauthorized);
    return () => window.removeEventListener("chargeflow:unauthorized", onUnauthorized);
  }, []);

  const login = useCallback(async (phone: string, password: string) => {
    const resp = await api.post<{ token: string }>("/auth/login", { phone, password });
    setToken(resp.token);
    setUser(await api.get<CurrentUser>("/auth/me"));
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

/* ---------------- 实时事件（WebSocket） ---------------- */

export interface LiveEvent {
  type: string;
  data: Record<string, unknown>;
  ts: number;
}

type Listener = (e: LiveEvent) => void;

interface LiveState {
  connected: boolean;
  subscribe: (types: string[], listener: Listener) => () => void;
}

const LiveContext = createContext<LiveState>(null as unknown as LiveState);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const listenersRef = useRef(new Set<{ types: string[]; listener: Listener }>());
  const { user } = useAuth();

  const subscribe = useCallback((types: string[], listener: Listener) => {
    const entry = { types, listener };
    listenersRef.current.add(entry);
    return () => listenersRef.current.delete(entry);
  }, []);

  useEffect(() => {
    const token = getToken();
    // 依赖 user 变化重连：登录建立连接，退出断开
    if (!token || !user) return;
    let closed = false;
    let retry = 0;

    let active: WebSocket | null = null;

    const connect = () => {
      if (closed) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws?token=${token}`);
      active = ws;
      const heartbeat = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 15000);
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data as string) as LiveEvent;
          for (const { types, listener } of listenersRef.current) {
            if (types.includes(event.type) || types.includes("*")) listener(event);
          }
        } catch {
          /* 非 JSON 帧 */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        window.clearInterval(heartbeat);
        if (!closed) {
          retry += 1;
          window.setTimeout(connect, Math.min(1000 * 2 ** retry, 15000));
        }
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      closed = true;
      active?.close();
    };
  }, [user]);

  return (
    <LiveContext.Provider value={{ connected, subscribe }}>{children}</LiveContext.Provider>
  );
}

export function useLive(): LiveState {
  return useContext(LiveContext);
}

export function useLiveEvents(types: string[], listener: Listener) {
  const { subscribe } = useContext(LiveContext);
  const listenerRef = useRef(listener);
  listenerRef.current = listener;
  useEffect(() => {
    return subscribe(types, (e) => listenerRef.current(e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [types.join(",")]);
}
