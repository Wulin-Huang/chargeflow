import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MapPin } from "lucide-react";
import { api } from "../api";
import { useLiveEvents } from "../store";

interface Station {
  id: number;
  name: string;
  address: string;
  lat: number;
  lng: number;
  total_piles: number;
  free_piles: number;
  charging_piles: number;
  price_hint: string;
}

interface Pile {
  id: number;
  code: string;
  connector: string;
  max_power_kw: number;
  status: string;
}

interface MyReservation {
  id: number;
  pile_id: number;
  start_at: string;
  end_at: string;
  status: string;
}

export const PILE_STATUS_META: Record<string, { label: string; tag: string; dot: string }> = {
  idle: { label: "空闲", tag: "green", dot: "green" },
  starting: { label: "启动中", tag: "cyan", dot: "cyan" },
  charging: { label: "充电中", tag: "cyan", dot: "cyan" },
  occupied: { label: "占位中", tag: "amber", dot: "amber" },
  reserved: { label: "已预约", tag: "amber", dot: "amber" },
  fault: { label: "故障", tag: "red", dot: "red" },
  offline: { label: "离线", tag: "gray", dot: "" },
};

export function PileTag({ status }: { status: string }) {
  const meta = PILE_STATUS_META[status] ?? PILE_STATUS_META.offline;
  return (
    <span className={`tag ${meta.tag}`}>
      <span className={`dot ${meta.dot}${status === "charging" || status === "starting" ? " pulse" : ""}`} />
      {meta.label}
    </span>
  );
}

function startLocalTime(offsetMin: number): string {
  const d = new Date(Date.now() + offsetMin * 60000);
  d.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function Stations() {
  const navigate = useNavigate();
  const [stations, setStations] = useState<Station[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [piles, setPiles] = useState<Pile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reservePile, setReservePile] = useState<Pile | null>(null);
  const [reservations, setReservations] = useState<MyReservation[]>([]);
  const [rStart, setRStart] = useState(startLocalTime(10));
  const [rEnd, setREnd] = useState(startLocalTime(70));

  const loadStations = useCallback(() => {
    api.get<Station[]>("/stations").then(setStations).catch(() => {});
  }, []);

  const loadPiles = useCallback((stationId: number) => {
    api.get<Pile[]>(`/stations/${stationId}/piles`).then(setPiles).catch(() => {});
  }, []);

  const loadReservations = useCallback(() => {
    api.get<MyReservation[]>("/reservations/mine").then(setReservations).catch(() => {});
  }, []);

  useEffect(() => {
    loadStations();
    loadReservations();
    const t = window.setInterval(loadStations, 15000);
    return () => window.clearInterval(t);
  }, [loadStations, loadReservations]);

  useEffect(() => {
    if (selected) loadPiles(selected);
  }, [selected, loadPiles]);

  useLiveEvents(["pile_update", "session_update"], () => {
    if (selected) loadPiles(selected);
    loadStations();
  });

  const startCharging = async (pile: Pile) => {
    setBusy(true);
    setError("");
    try {
      const rid = `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      await api.post("/sessions/start", { pile_id: pile.id, request_id: rid });
      navigate("/charging");
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动失败");
    } finally {
      setBusy(false);
    }
  };

  const submitReservation = async () => {
    if (!reservePile) return;
    setBusy(true);
    setError("");
    try {
      await api.post("/reservations", {
        pile_id: reservePile.id,
        start_at: rStart,
        end_at: rEnd,
      });
      setReservePile(null);
      loadReservations();
      if (selected) loadPiles(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : "预约失败");
    } finally {
      setBusy(false);
    }
  };

  const cancelReservation = async (id: number) => {
    await api.post(`/reservations/${id}/cancel`).catch(() => {});
    loadReservations();
    if (selected) loadPiles(selected);
  };

  return (
    <div>
      <div className="page-head">
        <span className="page-title">站点与充电</span>
        <span className="page-sub">数据由桩模拟器经 MQTT 实时上报，AI 生成的车型参数驱动仿真</span>
      </div>
      {error && <div className="toast error">{error}</div>}

      <div className="grid grid-3" style={{ marginBottom: 18 }}>
        {stations.map((st) => (
          <div
            key={st.id}
            className="card"
            style={{
              cursor: "pointer",
              borderColor: selected === st.id ? "var(--accent)" : undefined,
              boxShadow: selected === st.id ? "0 0 0 3px rgba(37,99,235,0.14), var(--shadow-sm)" : undefined,
            }}
            onClick={() => setSelected(st.id)}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{st.name}</div>
              <span className="tag cyan">{st.price_hint || "标准电价"}</span>
            </div>
            <div
              style={{
                color: "var(--text-dim)",
                fontSize: 12.5,
                margin: "6px 0 12px",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <MapPin size={12} color="var(--text-dim)" />
              {st.address}
            </div>
            <div style={{ display: "flex", gap: 18 }}>
              <div>
                <span className="kpi-value" style={{ color: st.free_piles > 0 ? "var(--green)" : "var(--red)" }}>
                  {st.free_piles}
                </span>
                <span style={{ color: "var(--text-dim)", fontSize: 12 }}> / {st.total_piles} 空闲</span>
              </div>
              <div>
                <span className="kpi-value" style={{ fontSize: 20 }}>{st.charging_piles}</span>
                <span style={{ color: "var(--text-dim)", fontSize: 12 }}> 充电中</span>
              </div>
            </div>
          </div>
        ))}
        {stations.length === 0 && <div className="card empty">暂无站点</div>}
      </div>

      {selected && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-title">桩位分布 · {stations.find((s) => s.id === selected)?.name}</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(168px, 1fr))", gap: 10 }}>
            {piles.map((p) => {
              const canCharge = p.status === "idle" && p.connector === "DC";
              return (
                <div
                  key={p.id}
                  style={{
                    background: "#f8fafd",
                    border: `1px solid ${canCharge ? "rgba(5,150,105,0.38)" : "var(--border)"}`,
                    borderRadius: 11,
                    padding: "12px 14px",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span className="mono" style={{ fontWeight: 600 }}>{p.code}</span>
                    <PileTag status={p.status} />
                  </div>
                  <div style={{ color: "var(--text-dim)", fontSize: 12, margin: "6px 0 10px" }}>
                    {p.connector === "DC" ? "直流快充" : "交流慢充"} · {p.max_power_kw}kW
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      className="btn small primary"
                      disabled={!canCharge || busy}
                      onClick={() => startCharging(p)}
                    >
                      开始充电
                    </button>
                    <button
                      className="btn small"
                      disabled={p.status !== "idle"}
                      onClick={() => setReservePile(p)}
                    >
                      预约
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ color: "var(--text-dim)", fontSize: 12, marginTop: 10 }}>
            直流桩支持扫码即充；交流桩演示预约流程（Redis 锁 + 唯一索引双层防并发）
          </div>
        </div>
      )}

      {reservations.length > 0 && (
        <div className="card">
          <div className="card-title">我的预约</div>
          <table className="tbl">
            <thead>
              <tr>
                <th>预约单</th>
                <th>桩</th>
                <th>时间窗</th>
                <th>状态</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {reservations.map((r) => (
                <tr key={r.id}>
                  <td className="mono">#{r.id}</td>
                  <td>{piles.find((p) => p.id === r.pile_id)?.code ?? `P${String(r.pile_id).padStart(3, "0")}`}</td>
                  <td>
                    {new Date(r.start_at).toLocaleString("zh-CN", { hour12: false })} ~{" "}
                    {new Date(r.end_at).toLocaleTimeString("zh-CN", { hour12: false })}
                  </td>
                  <td>
                    <span
                      className={`tag ${
                        r.status === "pending" ? "amber" : r.status === "active" ? "cyan" : "gray"
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td>
                    {(r.status === "pending" || r.status === "active") && (
                      <button className="btn small danger" onClick={() => cancelReservation(r.id)}>
                        取消
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reservePile && (
        <div className="modal-mask" onClick={() => setReservePile(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">
              预约 {reservePile.code} · {reservePile.connector === "DC" ? "直流" : "交流"} {reservePile.max_power_kw}kW
            </div>
            <div style={{ display: "grid", gap: 10 }}>
              <div>
                <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 6 }}>开始时间</div>
                <input className="input" type="datetime-local" value={rStart} onChange={(e) => setRStart(e.target.value)} />
              </div>
              <div>
                <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 6 }}>结束时间</div>
                <input className="input" type="datetime-local" value={rEnd} onChange={(e) => setREnd(e.target.value)} />
              </div>
              {error && <div style={{ color: "var(--red)", fontSize: 12.5 }}>{error}</div>}
              <button className="btn primary" disabled={busy} onClick={submitReservation}>
                确认预约
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
