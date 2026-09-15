import { useCallback, useEffect, useRef, useState } from "react";
import { Radio, Zap, Gauge, CircleDollarSign } from "lucide-react";
import { api } from "../api";
import { useAuth, useLiveEvents } from "../store";
import EChart, { AXIS, TOOLTIP } from "../components/EChart";

interface Stats {
  sessions_today: number;
  energy_kwh_today: number;
  revenue_yuan_today: number;
  open_work_orders: number;
  pile_status: Record<string, number>;
  ws_connections: number;
  telemetry_pending: number;
}

interface Kpi {
  piles_total: number;
  piles_online: number;
  charging_sessions: number;
  total_power_kw: number;
}

interface WoDiag {
  attempted?: boolean;
  degraded?: boolean;
  reason?: string;
  root_cause?: string;
  action?: string;
  confidence?: number;
}

interface WorkOrder {
  id: number;
  pile_id: number;
  type: string;
  severity: string;
  status: string;
  trigger_rule: string;
  ai_diagnosis: WoDiag | null;
  created_at: string;
}

function diagText(d: WoDiag | null): string {
  if (!d) return "诊断中…";
  if (d.degraded) return d.reason ?? "AI 诊断不可用，请人工分析";
  if (d.root_cause) return d.root_cause.slice(0, 80);
  return d.attempted ? "诊断中…" : "待诊断";
}

interface Station {
  id: number;
  name: string;
  total_piles: number;
  free_piles: number;
  charging_piles: number;
}

interface LoadStation {
  station_id: number;
  name: string;
  capacity_kw: number;
  demand_kw: number;
  charging_piles: number;
  curbed: boolean;
  shaved_kw: number;
}

const MAX_POINTS = 72;
const WO_TYPE_LABEL: Record<string, string> = {
  overtemp: "枪温过高",
  power_drop: "功率骤降",
  offline: "设备离线",
  device_fault: "设备故障",
};

export default function Dashboard() {
  const { user } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [stations, setStations] = useState<Station[]>([]);
  const [kpi, setKpi] = useState<Kpi | null>(null);
  const [powerHistory, setPowerHistory] = useState<{ t: number; kw: number }[]>([]);
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>([]);
  const [feeding, setFeeding] = useState<{ text: string; ts: number }[]>([]);
  const [loadStations, setLoadStations] = useState<LoadStation[]>([]);
  const kpiRef = useRef(kpi);
  kpiRef.current = kpi;

  const load = useCallback(() => {
    api.get<Stats>("/admin/stats").then(setStats).catch(() => {});
    api.get<Station[]>("/stations").then(setStations).catch(() => {});
    api.get<WorkOrder[]>("/admin/work-orders").then(setWorkOrders).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 20000);
    return () => window.clearInterval(t);
  }, [load]);

  useLiveEvents(["kpi"], (e) => {
    const d = e.data as unknown as Kpi;
    setKpi(d);
    setPowerHistory((pts) => {
      const next = [...pts, { t: Date.now(), kw: d.total_power_kw }];
      return next.slice(-MAX_POINTS);
    });
  });
  useLiveEvents(["work_order"], (e) => {
    const d = e.data as { id?: number; pile_id?: number; type?: string };
    setFeeding((f) =>
      [
        {
          text: `工单 #${d.id} · P${String(d.pile_id ?? 0).padStart(3, "0")} · ${WO_TYPE_LABEL[d.type ?? ""] ?? d.type}`,
          ts: Date.now(),
        },
        ...f,
      ].slice(0, 12)
    );
    api.get<WorkOrder[]>("/admin/work-orders").then(setWorkOrders).catch(() => {});
  });
  useLiveEvents(["load_status"], (e) => {
    const d = e.data as unknown as { stations?: LoadStation[] };
    if (d.stations) {
      setLoadStations([...d.stations].sort((a, b) => b.demand_kw / Math.max(1, b.capacity_kw) - a.demand_kw / Math.max(1, a.capacity_kw)));
    }
  });
  useLiveEvents(["power_dispatch"], (e) => {
    const d = e.data as { pile_id?: number; limit_kw?: number };
    if (d.limit_kw === 0) {
      setFeeding((f) => [{ text: `调度解除 · P${String(d.pile_id ?? 0).padStart(3, "0")} 恢复满功率`, ts: Date.now() }, ...f].slice(0, 12));
    } else {
      setFeeding((f) =>
        [{ text: `有序充电 · P${String(d.pile_id ?? 0).padStart(3, "0")} 限功率 ${d.limit_kw}kW`, ts: Date.now() }, ...f].slice(0, 12)
      );
    }
  });
  useLiveEvents(["session_update"], (e) => {
    const d = e.data as { session_id?: number; status?: string };
    setFeeding((f) =>
      [
        { text: `会话 #${d.session_id} → ${d.status}`, ts: Date.now() },
        ...f,
      ].slice(0, 12)
    );
  });

  const statusDist = stats?.pile_status ?? {};
  const donutOption = {
    tooltip: { ...TOOLTIP },
    legend: { bottom: 0, textStyle: { color: "#62708c", fontSize: 11 } },
    series: [
      {
        type: "pie" as const,
        radius: ["52%", "72%"],
        center: ["50%", "42%"],
        label: { show: false },
        data: Object.entries(statusDist).map(([name, value]) => {
          const colorMap: Record<string, string> = {
            idle: "#10b981",
            charging: "#0ea5e9",
            starting: "#0ea5e9",
            occupied: "#f59e0b",
            reserved: "#f59e0b",
            fault: "#ef4444",
            offline: "#94a3b8",
          };
          return { name, value, itemStyle: { color: colorMap[name] ?? "#94a3b8" } };
        }),
      },
    ],
  };

  const powerOption = {
    grid: { left: 52, right: 20, top: 24, bottom: 26 },
    tooltip: { trigger: "axis" as const, ...TOOLTIP },
    xAxis: {
      type: "time" as const,
      ...AXIS,
      axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => new Date(v).toLocaleTimeString("zh-CN", { hour12: false, minute: "2-digit", second: "2-digit" }) },
    },
    yAxis: { type: "value" as const, name: "kW", ...AXIS },
    series: [
      {
        name: "平台总功率",
        type: "line" as const,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, color: "#059669" },
        areaStyle: { color: "rgba(5,150,105,0.09)" },
        data: powerHistory.map((p) => [p.t, p.kw]),
      },
    ],
  };

  return (
    <div>
      <div className="page-head">
        <span className="page-title">运营大屏</span>
        <span className="page-sub">
          {user?.nickname} · KPI 每 5s 聚合推送 · 遥测逐秒入库
          {stats ? ` · WS 连接 ${stats.ws_connections} · 遥测缓冲 ${stats.telemetry_pending}` : ""}
        </span>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <Radio size={14} color="var(--accent)" /> 在线桩数（MQTT 心跳）
          </div>
          <div className="kpi-value" style={{ color: kpi ? "var(--accent)" : undefined }}>
            {kpi?.piles_online ?? "–"}
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}> / {kpi?.piles_total ?? "–"}</span>
          </div>
        </div>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <Zap size={14} color="var(--green)" /> 进行中充电会话
          </div>
          <div className="kpi-value" style={{ color: "var(--green)" }}>{kpi?.charging_sessions ?? 0}</div>
        </div>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <Gauge size={14} color="var(--cyan)" /> 平台实时总功率
          </div>
          <div className="kpi-value">{(kpi?.total_power_kw ?? 0).toFixed(1)}<span style={{ fontSize: 13, color: "var(--text-dim)" }}> kW</span></div>
        </div>
        <div className="card">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 0 }}>
            <CircleDollarSign size={14} color="var(--amber)" /> 今日营收
          </div>
          <div className="kpi-value" style={{ color: "var(--amber)" }}>¥{stats?.revenue_yuan_today?.toFixed(2) ?? "0.00"}</div>
        </div>
      </div>

      <div className="grid grid-3" style={{ marginBottom: 16, gridTemplateColumns: "1.4fr 1fr 1fr" }}>
        <div className="card">
          <div className="card-title">平台总功率曲线（实时）</div>
          <EChart option={powerOption} height={240} />
        </div>
        <div className="card">
          <div className="card-title">桩状态分布</div>
          <EChart option={donutOption} height={240} />
        </div>
        <div className="card" style={{ display: "flex", flexDirection: "column" }}>
          <div className="card-title">实时事件流</div>
          <div style={{ flex: 1, overflowY: "auto", fontSize: 12.5, color: "var(--text-dim)" }}>
            {feeding.length === 0 && <div className="empty">等待事件…</div>}
            {feeding.map((f, i) => (
              <div key={f.ts + i} style={{ padding: "4px 0", borderBottom: "1px solid #eef2f8" }}>
                <span className="mono" style={{ color: "var(--accent)" }}>
                  {new Date(f.ts).toLocaleTimeString("zh-CN", { hour12: false })}
                </span>{" "}
                {f.text}
              </div>
            ))}
          </div>
          <div style={{ marginTop: 8 }}>
            <div style={{ color: "var(--text-dim)", fontSize: 11.5 }}>
              今日 {stats?.sessions_today ?? 0} 单 · {stats?.energy_kwh_today ?? 0} kWh
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">
          台区有序充电 · 负荷调度（每 6s 水位填充公平份额）
          {loadStations.some((s) => s.curbed) ? (
            <span className="tag amber" style={{ marginLeft: "auto" }}>
              {loadStations.filter((s) => s.curbed).length} 站超容限功率中
            </span>
          ) : (
            <span className="tag green" style={{ marginLeft: "auto" }}>全部台区负载正常</span>
          )}
        </div>
        {loadStations.length === 0 ? (
          <div className="empty">等待负荷上报——有充电会话的站点会每 6 秒推送需求/容量</div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>站点</th>
                <th>实时需求</th>
                <th>台区容量</th>
                <th style={{ width: "34%" }}>负载率</th>
                <th>充电桩</th>
                <th>调度状态</th>
              </tr>
            </thead>
            <tbody>
              {loadStations.slice(0, 10).map((st) => {
                const pct = Math.min(100, Math.round((st.demand_kw / Math.max(1, st.capacity_kw)) * 100));
                const color = st.curbed
                  ? "linear-gradient(90deg,#ef4444,#f59e0b)"
                  : pct > 85
                    ? "linear-gradient(90deg,#f59e0b,#fbbf24)"
                    : "linear-gradient(90deg,#10b981,#34d399)";
                return (
                  <tr key={st.station_id}>
                    <td>{st.name}</td>
                    <td className="mono">{st.demand_kw.toFixed(1)} kW</td>
                    <td className="mono" style={{ color: "var(--text-dim)" }}>{st.capacity_kw} kW</td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{ background: "#edf2f8", borderRadius: 6, height: 8, overflow: "hidden", flex: 1 }}>
                          <div style={{ height: "100%", width: `${pct}%`, background: color }} />
                        </div>
                        <span className="mono" style={{ fontSize: 12, color: "var(--text-dim)", width: 38 }}>{pct}%</span>
                      </div>
                    </td>
                    <td className="mono">{st.charging_piles}</td>
                    <td>
                      {st.curbed ? (
                        <span className="tag red">限功率 · 削峰 {st.shaved_kw.toFixed(1)}kW</span>
                      ) : (
                        <span className="tag green">正常</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">站点运营概览</div>
          <table className="tbl">
            <thead>
              <tr>
                <th>站点</th>
                <th>总桩数</th>
                <th>空闲</th>
                <th>充电中</th>
                <th>使用率</th>
              </tr>
            </thead>
            <tbody>
              {stations.map((st) => (
                <tr key={st.id}>
                  <td>{st.name}</td>
                  <td className="mono">{st.total_piles}</td>
                  <td className="mono" style={{ color: st.free_piles > 0 ? "var(--green)" : "var(--red)" }}>
                    {st.free_piles}
                  </td>
                  <td className="mono">{st.charging_piles}</td>
                  <td>
                    <div style={{ background: "#edf2f8", borderRadius: 6, height: 8, overflow: "hidden" }}>
                      <div
                        style={{
                          height: "100%",
                          width: `${Math.round(((st.total_piles - st.free_piles) / Math.max(1, st.total_piles)) * 100)}%`,
                          background: "linear-gradient(90deg,#2563eb,#06b6d4)",
                        }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <div className="card-title">
            工单队列（规则引擎触发 · AI 自动定因）
            {stats?.open_work_orders ? (
              <span className="tag amber" style={{ marginLeft: "auto" }}>{stats.open_work_orders} 待处理</span>
            ) : null}
          </div>
          {workOrders.length === 0 ? (
            <div className="empty">暂无工单——混沌模式运行中，异常会自动生成工单并触发 AI 诊断</div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>工单</th>
                  <th>桩</th>
                  <th>类型</th>
                  <th>AI 诊断</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {workOrders.slice(0, 8).map((w) => (
                  <tr key={w.id}>
                    <td className="mono">#{w.id}</td>
                    <td className="mono">P{String(w.pile_id).padStart(3, "0")}</td>
                    <td>
                      <span className={`tag ${w.status === "open" ? "red" : "gray"}`}>
                        {WO_TYPE_LABEL[w.type] ?? w.type}
                      </span>
                    </td>
                    <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-dim)" }}>
                      {diagText(w.ai_diagnosis)}
                    </td>
                    <td>{w.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
