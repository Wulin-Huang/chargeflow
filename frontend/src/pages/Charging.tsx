import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Zap, BatteryCharging, Plug, Wallet, PlusCircle, AlertTriangle, Target, Clock, Settings } from "lucide-react";
import { api } from "../api";
import { useLiveEvents } from "../store";
import EChart, { AXIS, TOOLTIP } from "../components/EChart";

interface SessionOut {
  id: number;
  pile_id: number;
  status: string;
  start_at: string | null;
  end_at: string | null;
  kwh_wh: number;
  stop_reason: string;
}

interface WalletInfo {
  balance_cents: number;
  arrears: boolean;
  recent_orders: { order_id: number; session_id: number; pile_id: number; total_cents: number; created_at: string }[];
}

interface TelemetryPoint {
  t: number;
  power_kw: number;
  soc: number;
}

interface Bill {
  session_id: number;
  settled: boolean;
  est_total_cents?: number;
  cum_wh?: number;
  total_cents?: number;
  energy_fee_cents?: number;
  service_fee_cents?: number;
  occupy_fee_cents?: number;
  segments?: { start: string; end: string; kwh: number; price_cents: number; fee_cents: number }[];
}

const MAX_POINTS = 120;

export default function Charging() {
  const [session, setSession] = useState<SessionOut | null>(null);
  const [bill, setBill] = useState<Bill | null>(null);
  const [points, setPoints] = useState<TelemetryPoint[]>([]);
  const [busy, setBusy] = useState(false);
  const [wallet, setWallet] = useState<WalletInfo | null>(null);
  const [showTarget, setShowTarget] = useState(false);
  const [targetMode, setTargetMode] = useState<"soc" | "amount">("soc");
  const [targetSoc, setTargetSoc] = useState(80);
  const [targetYuan, setTargetYuan] = useState(50);
  const [targetInfo, setTargetInfo] = useState<{ target_soc: number | null; target_cents: number | null; eta_minutes: number | null } | null>(null);
  const [targetBusy, setTargetBusy] = useState(false);

  const refresh = useCallback(async () => {
    const s = await api.get<SessionOut | null>("/sessions/active").catch(() => null);
    setSession(s);
    if (s) {
      const b = await api.get<Bill>(`/sessions/${s.id}/bill`).catch(() => null);
      setBill(b);
    }
    setWallet(await api.get<WalletInfo>("/wallet").catch(() => null));
  }, []);

  const recharge = async (yuan: number) => {
    try {
      await api.post("/wallet/recharge", { amount_cents: yuan * 100 });
      setWallet(await api.get<WalletInfo>("/wallet"));
    } catch {
      /* 网络错误静默 */
    }
  };

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 5000);
    return () => window.clearInterval(t);
  }, [refresh]);

  const onTelemetry = useCallback((e: { data: Record<string, unknown> }) => {
    const d = e.data;
    const pileId = d.pile_id as number;
    setSession((cur) => {
      if (cur && cur.pile_id === pileId && cur.status === "charging") {
        setPoints((pts) => {
          const next = [...pts, { t: Date.now(), power_kw: d.power_kw as number, soc: d.soc as number }];
          return next.slice(-MAX_POINTS);
        });
      }
      return cur;
    });
  }, []);

  useLiveEvents(["telemetry"], onTelemetry);
  useLiveEvents(["session_update", "bill"], () => {
    refresh();
  });

  const stop = async () => {
    if (!session) return;
    setBusy(true);
    try {
      await api.post(`/sessions/${session.id}/stop`);
      setTimeout(refresh, 2000);
    } finally {
      setBusy(false);
    }
  };

  const walletCard = wallet ? (
    <div className="card" style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Wallet size={16} color="var(--amber)" />
        <span style={{ fontWeight: 600 }}>
          余额 ¥{((wallet.balance_cents < 0 ? 0 : wallet.balance_cents) / 100).toFixed(2)}
        </span>
        {wallet.arrears && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--red)", fontSize: 12.5 }}>
            <AlertTriangle size={13} /> 欠费 ¥{((-wallet.balance_cents) / 100).toFixed(2)}，需充值后才能启动充电
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
        {[20, 50, 100].map((y) => (
          <button key={y} className="btn small" onClick={() => recharge(y)}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <PlusCircle size={13} /> ¥{y}
            </span>
          </button>
        ))}
      </div>
      {wallet.recent_orders.length > 0 && (
        <div style={{ width: "100%", fontSize: 12, color: "var(--text-dim)", borderTop: "1px solid #eef2f8", paddingTop: 8 }}>
          最近消费：{wallet.recent_orders.slice(0, 3).map((o) => `会话#${o.session_id} ¥${(o.total_cents / 100).toFixed(2)}`).join(" · ")}
        </div>
      )}
    </div>
  ) : null;

  if (!session) {
    return (
      <div>
        <div className="page-head">
          <span className="page-title">充电监控</span>
        </div>
        {walletCard}
        <div className="card empty">
          当前没有进行中的充电会话。
          <div style={{ marginTop: 10 }}>
            <Link to="/">去选一个空闲桩开始充电 →</Link>
          </div>
        </div>
      </div>
    );
  }

  const cumKwh = ((bill?.cum_wh ?? 0) / 1000).toFixed(2);
  const estYuan = (((bill?.settled ? bill.total_cents : bill?.est_total_cents) ?? 0) / 100).toFixed(2);
  const last = points[points.length - 1];
  const powerKw = last?.power_kw ?? 0;
  const soc = last?.soc ?? 0;
  const charging = session.status === "charging";

  const chartOption = {
    grid: { left: 48, right: 48, top: 30, bottom: 28 },
    tooltip: { trigger: "axis" as const, ...TOOLTIP },
    xAxis: {
      type: "time" as const,
      ...AXIS,
      axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => new Date(v).toLocaleTimeString("zh-CN", { hour12: false, minute: "2-digit", second: "2-digit" }) },
    },
    yAxis: [
      { type: "value" as const, name: "kW", max: 250, ...AXIS },
      { type: "value" as const, name: "SOC%", max: 100, ...AXIS, splitLine: { show: false } },
    ],
    series: [
      {
        name: "功率 kW",
        type: "line" as const,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, color: "#2563eb" },
        areaStyle: { color: "rgba(37,99,235,0.1)" },
        data: points.map((p) => [p.t, p.power_kw]),
      },
      {
        name: "SOC %",
        type: "line" as const,
        yAxisIndex: 1,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.5, color: "#059669", type: "dashed" as const },
        data: points.map((p) => [p.t, p.soc]),
      },
    ],
  };

  return (
    <div>
      <div className="page-head">
        <span className="page-title">充电监控</span>
        <span className="page-sub">
          会话 <span className="mono">#{session.id}</span> · 桩 <span className="mono">P{String(session.pile_id).padStart(3, "0")}</span> ·{" "}
          <span className={`tag ${charging ? "cyan" : session.status === "occupied" ? "amber" : "gray"}`}>
            {session.status}
          </span>
        </span>
      </div>

      {walletCard}

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <Zap size={14} color="var(--accent)" /> 实时功率
          </div>
          <div className="kpi-value" style={{ color: charging ? "var(--accent)" : "var(--text-dim)" }}>
            {powerKw.toFixed(1)}
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}> kW</span>
          </div>
        </div>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <BatteryCharging size={14} color="var(--green)" /> 电池 SOC
          </div>
          <div className="kpi-value">{soc.toFixed(1)}<span style={{ fontSize: 13, color: "var(--text-dim)" }}> %</span></div>
        </div>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <Plug size={14} color="var(--cyan)" /> 已充电量（分时积分）
          </div>
          <div className="kpi-value">{cumKwh}<span style={{ fontSize: 13, color: "var(--text-dim)" }}> kWh</span></div>
        </div>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <Wallet size={14} color="var(--amber)" /> {bill?.settled ? "最终账单" : "实时预估"}
          </div>
          <div className="kpi-value" style={{ color: "var(--green)" }}>¥{estYuan}</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">
          <Target size={14} color="var(--green)" style={{ marginRight: 5 }} /> 充电目标设置
          <button className="btn small" style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4 }} onClick={() => setShowTarget(true)}>
            <Settings size={12} /> 设置目标
          </button>
        </div>
        {targetInfo?.target_soc || targetInfo?.target_cents ? (
          <div className="grid grid-2">
            {targetInfo.target_soc && (
              <div>
                <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 4 }}>SOC 目标</div>
                <div style={{ fontSize: 22, fontWeight: 600, color: "var(--green)" }}>
                  {targetInfo.target_soc}%
                </div>
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  达到后自动停止
                </div>
              </div>
            )}
            {targetInfo.target_cents && (
              <div>
                <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 4 }}>金额目标</div>
                <div style={{ fontSize: 22, fontWeight: 600, color: "var(--amber)" }}>
                  ¥{(targetInfo.target_cents / 100).toFixed(2)}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  预估费用达到即停止
                </div>
              </div>
            )}
            {targetInfo.eta_minutes && (
              <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  <Clock size={12} style={{ verticalAlign: -1, marginRight: 3 }} />
                  预计还需 {Math.floor(targetInfo.eta_minutes / 60)} 小时 {targetInfo.eta_minutes % 60} 分钟充满到目标
                </div>
              </div>
            )}
          </div>
        ) : (
          <div style={{ color: "var(--text-dim)", fontSize: 13 }}>
            暂未设置充电目标。设置 SOC 或金额目标后，达到目标系统会自动停止充电。
            <button className="btn small primary" style={{ marginLeft: 12 }} onClick={() => setShowTarget(true)}>
              设置目标
            </button>
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">实时遥测曲线（WebSocket 推送，每秒一帧）</div>
        <EChart option={chartOption} height={280} />
        {points.length === 0 && (
          <div className="empty" style={{ marginTop: -180, pointerEvents: "none" }}>
            等待遥测数据流入…
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-title">账单明细</div>
        {bill?.settled ? (
          <table className="tbl">
            <tbody>
              <tr>
                <td style={{ color: "var(--text-dim)" }}>电费</td>
                <td className="mono" style={{ textAlign: "right" }}>¥{((bill.energy_fee_cents ?? 0) / 100).toFixed(2)}</td>
              </tr>
              <tr>
                <td style={{ color: "var(--text-dim)" }}>服务费</td>
                <td className="mono" style={{ textAlign: "right" }}>¥{((bill.service_fee_cents ?? 0) / 100).toFixed(2)}</td>
              </tr>
              <tr>
                <td style={{ color: "var(--text-dim)" }}>占位费</td>
                <td className="mono" style={{ textAlign: "right" }}>¥{((bill.occupy_fee_cents ?? 0) / 100).toFixed(2)}</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>合计</td>
                <td className="mono" style={{ textAlign: "right", color: "var(--green)", fontWeight: 700 }}>
                  ¥{((bill.total_cents ?? 0) / 100).toFixed(2)}
                </td>
              </tr>
            </tbody>
          </table>
        ) : (
          <div className="empty">
            {charging ? "充电进行中，结束充电后生成分时账单" : "等待结算…"}
          </div>
        )}
      </div>

      {(charging || session.status === "starting") && (
        <button
          className="btn danger"
          style={{ marginTop: 16, padding: "10px 28px" }}
          disabled={busy}
          onClick={stop}
        >
          结束充电
        </button>
      )}

      {showTarget && session && (
        <div className="modal-mask" onClick={() => setShowTarget(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 400 }}>
            <div className="modal-title">设置充电目标</div>

            <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
              <button
                className={`btn small${targetMode === "soc" ? " primary" : ""}`}
                onClick={() => setTargetMode("soc")}
                style={{ flex: 1 }}
              >
                <BatteryCharging size={13} style={{ marginRight: 3 }} /> 按 SOC%
              </button>
              <button
                className={`btn small${targetMode === "amount" ? " primary" : ""}`}
                onClick={() => setTargetMode("amount")}
                style={{ flex: 1 }}
              >
                <Wallet size={13} style={{ marginRight: 3 }} /> 按金额
              </button>
            </div>

            {targetMode === "soc" ? (
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 12, color: "var(--text-dim)" }}>目标电量</span>
                  <span className="mono" style={{ fontWeight: 600, color: "var(--green)" }}>{targetSoc}%</span>
                </div>
                <input
                  type="range"
                  min={20}
                  max={100}
                  step={5}
                  value={targetSoc}
                  onChange={(e) => setTargetSoc(Number(e.target.value))}
                  style={{ width: "100%" }}
                />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
                  <span>20%</span><span>50%</span><span>80%</span><span>100%</span>
                </div>
                <div style={{ marginTop: 10, fontSize: 12, color: "var(--text-dim)" }}>
                  充电到 {targetSoc}% 时自动停止（保护电池健康，建议日常充到 80%）
                </div>
              </div>
            ) : (
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 12, color: "var(--text-dim)" }}>目标金额</span>
                  <span className="mono" style={{ fontWeight: 600, color: "var(--amber)" }}>¥{targetYuan}</span>
                </div>
                <input
                  type="range"
                  min={10}
                  max={200}
                  step={10}
                  value={targetYuan}
                  onChange={(e) => setTargetYuan(Number(e.target.value))}
                  style={{ width: "100%" }}
                />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
                  <span>¥10</span><span>¥50</span><span>¥100</span><span>¥200</span>
                </div>
                <div style={{ marginTop: 10, fontSize: 12, color: "var(--text-dim)" }}>
                  预估费用达到 ¥{targetYuan} 时自动停止（以最终结算账单为准）
                </div>
              </div>
            )}

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
              <button className="btn small" onClick={() => setShowTarget(false)}>取消</button>
              <button
                className="btn small primary"
                disabled={targetBusy}
                onClick={async () => {
                  if (!session) return;
                  setTargetBusy(true);
                  try {
                    const body: any = {};
                    if (targetMode === "soc") body.target_soc = targetSoc;
                    else body.target_cents = Math.round(targetYuan * 100);
                    const r = await api.patch<{ target_soc: number | null; target_cents: number | null; eta_minutes: number | null }>(`/sessions/${session.id}/target`, body);
                    setTargetInfo({ target_soc: r.target_soc, target_cents: r.target_cents, eta_minutes: r.eta_minutes });
                    setShowTarget(false);
                  } catch (err: any) {
                    alert(err?.response?.data?.detail || "设置失败");
                  } finally {
                    setTargetBusy(false);
                  }
                }}
              >
                {targetBusy ? "设置中…" : "确认设置"}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}