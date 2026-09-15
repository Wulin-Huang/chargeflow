import { useEffect, useState } from "react";
import { api } from "../api";
import EChart, { AXIS } from "../components/EChart";

interface Period {
  start: string;
  end: string;
  price_cents: number;
}

interface Policy {
  periods: Period[];
  service_fee_cents_per_kwh: number;
  free_occupy_minutes: number;
  occupy_fee_cents_per_min: number;
}

interface Station {
  id: number;
  name: string;
  price_hint: string;
}

interface VehicleProfile {
  id: number;
  model_name: string;
  battery_kwh: number;
  soc_curve: number[][];
  anomaly_profile: Record<string, number>;
}

interface AiLog {
  id: number;
  scene: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cache_hit_tokens: number;
  latency_ms: number;
  cost_usd: number;
  ok: number;
  created_at: string;
}

interface DailyReportRow {
  date: string;
  stats: Record<string, unknown>;
  narrative: string;
}

interface PileHealthRow {
  pile_id: number;
  code: string;
  station: string;
  score: number;
  grade: string;
  reasons: string[];
  suggestion: string;
  limit_kw: number | null;
}

const SCENE_LABEL: Record<string, string> = {
  profile_gen: "车型参数生成",
  assistant_tools: "助手·工具轮",
  assistant_final: "助手·生成轮",
  diagnose: "工单诊断",
  daily_report: "运营日报",
};

function CurvePreview({ curve }: { curve: number[][] }) {
  const option = {
    grid: { left: 30, right: 8, top: 10, bottom: 20 },
    xAxis: { type: "value" as const, max: 100, ...AXIS, axisLabel: { show: false } },
    yAxis: { type: "value" as const, ...AXIS, axisLabel: { show: false }, splitLine: { show: false } },
    series: [
      {
        type: "line" as const,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.5, color: "#2563eb" },
        areaStyle: { color: "rgba(37,99,235,0.12)" },
        data: curve,
      },
    ],
  };
  return <EChart option={option} height={64} />;
}

export default function Admin() {
  const [tab, setTab] = useState<"price" | "profiles" | "ai" | "report" | "health">("price");
  const [stations, setStations] = useState<Station[]>([]);
  const [stationId, setStationId] = useState<number | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [saving, setSaving] = useState(false);
  const [profiles, setProfiles] = useState<VehicleProfile[]>([]);
  const [genBusy, setGenBusy] = useState(false);
  const [aiLogs, setAiLogs] = useState<{ total_cost_usd: number; call_count: number; ok_rate: number; calls: AiLog[] } | null>(null);
  const [reports, setReports] = useState<DailyReportRow[]>([]);
  const [reportBusy, setReportBusy] = useState(false);
  const [health, setHealth] = useState<{ total: number; piles: PileHealthRow[] } | null>(null);
  const [toast, setToast] = useState("");

  useEffect(() => {
    api.get<Station[]>("/stations").then((sts) => {
      setStations(sts);
      if (sts.length) setStationId(sts[0].id);
    });
    api.get<VehicleProfile[]>("/admin/vehicle-profiles").then(setProfiles).catch(() => {});
    api.get<ReturnType<typeof Object>>("/ai/logs").then(setAiLogs).catch(() => {});
    api.get<DailyReportRow[]>("/admin/daily-reports").then(setReports).catch(() => {});
  }, []);

  useEffect(() => {
    if (stationId) {
      api.get<Policy>(`/admin/price-policies/${stationId}`).then(setPolicy).catch(() => setPolicy(null));
    }
  }, [stationId]);

  useEffect(() => {
    if (tab !== "health") return;
    const loadHealth = () => {
      api.get<{ total: number; piles: PileHealthRow[] }>("/admin/pile-health").then(setHealth).catch(() => {});
    };
    loadHealth();
    const t = window.setInterval(loadHealth, 30000);
    return () => window.clearInterval(t);
  }, [tab]);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(""), 2600);
  };

  const savePolicy = async () => {
    if (!stationId || !policy) return;
    setSaving(true);
    try {
      await api.post("/admin/price-policies", {
        station_id: stationId,
        periods: policy.periods,
        service_fee_cents_per_kwh: policy.service_fee_cents_per_kwh,
        free_occupy_minutes: policy.free_occupy_minutes,
        occupy_fee_cents_per_min: policy.occupy_fee_cents_per_min,
      });
      flash("电价策略已发布（新会话即生效，进行中的会话沿用旧契约）");
    } catch (err) {
      flash(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const updatePeriod = (i: number, patch: Partial<Period>) => {
    if (!policy) return;
    const periods = policy.periods.map((p, j) => (j === i ? { ...p, ...patch } : p));
    setPolicy({ ...policy, periods });
  };

  const generateProfiles = async () => {
    setGenBusy(true);
    try {
      const r = await api.post<{ generated: number; saved: number }>("/admin/ai/generate-profiles");
      const list = await api.get<VehicleProfile[]>("/admin/vehicle-profiles");
      setProfiles(list);
      flash(`AI 生成 ${r.generated} 个新车型，入库 ${r.saved} 个`);
    } catch (err) {
      flash(err instanceof Error ? err.message : "生成失败");
    } finally {
      setGenBusy(false);
    }
  };

  const generateReport = async () => {
    setReportBusy(true);
    try {
      await api.post("/admin/daily-report");
      const list = await api.get<DailyReportRow[]>("/admin/daily-reports");
      setReports(list);
      setTab("report");
      flash("日报已生成");
    } catch (err) {
      flash(err instanceof Error ? err.message : "生成失败");
    } finally {
      setReportBusy(false);
    }
  };

  return (
    <div>
      <div className="page-head">
        <span className="page-title">平台管理</span>
        <span className="page-sub">电价策略 · AI 车型库 · AI 成本观测 · 运营日报 · 桩健康度</span>
      </div>
      {toast && <div className="toast">{toast}</div>}

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {(
          [
            ["price", "电价策略"],
            ["profiles", "AI 车型库"],
            ["ai", "AI 调用观测"],
            ["report", "运营日报"],
            ["health", "桩健康度"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            className={`btn small${tab === key ? " primary" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "price" && (
        <div className="card">
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14 }}>
            <select
              className="select"
              style={{ width: 220 }}
              value={stationId ?? ""}
              onChange={(e) => setStationId(Number(e.target.value))}
            >
              {stations.map((st) => (
                <option key={st.id} value={st.id}>
                  {st.name}
                </option>
              ))}
            </select>
            <button className="btn primary" disabled={saving || !policy} onClick={savePolicy}>
              发布新策略
            </button>
          </div>
          {policy ? (
            <>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>时段起</th>
                    <th>时段止</th>
                    <th>电价（分/kWh）</th>
                    <th>折合</th>
                  </tr>
                </thead>
                <tbody>
                  {policy.periods.map((p, i) => (
                    <tr key={i}>
                      <td>
                        <input
                          className="input"
                          style={{ width: 110 }}
                          value={p.start}
                          onChange={(e) => updatePeriod(i, { start: e.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          className="input"
                          style={{ width: 110 }}
                          value={p.end}
                          onChange={(e) => updatePeriod(i, { end: e.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          className="input"
                          type="number"
                          style={{ width: 110 }}
                          value={p.price_cents}
                          onChange={(e) => updatePeriod(i, { price_cents: Number(e.target.value) })}
                        />
                      </td>
                      <td className="mono">¥{(p.price_cents / 100).toFixed(2)}/kWh</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="grid grid-3" style={{ marginTop: 14 }}>
                <div>
                  <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 6 }}>服务费（分/kWh）</div>
                  <input
                    className="input"
                    type="number"
                    value={policy.service_fee_cents_per_kwh}
                    onChange={(e) => setPolicy({ ...policy, service_fee_cents_per_kwh: Number(e.target.value) })}
                  />
                </div>
                <div>
                  <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 6 }}>免费占位时长（分钟）</div>
                  <input
                    className="input"
                    type="number"
                    value={policy.free_occupy_minutes}
                    onChange={(e) => setPolicy({ ...policy, free_occupy_minutes: Number(e.target.value) })}
                  />
                </div>
                <div>
                  <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 6 }}>占位费（分/分钟）</div>
                  <input
                    className="input"
                    type="number"
                    value={policy.occupy_fee_cents_per_min}
                    onChange={(e) => setPolicy({ ...policy, occupy_fee_cents_per_min: Number(e.target.value) })}
                  />
                </div>
              </div>
            </>
          ) : (
            <div className="empty">该站暂无策略</div>
          )}
        </div>
      )}

      {tab === "profiles" && (
        <div className="card">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
              DeepSeek 生成的虚构车型参数包驱动桩仿真——每次生成都不同（共 {profiles.length} 个）
            </span>
            <button className="btn primary small" disabled={genBusy} onClick={generateProfiles} style={{ marginLeft: "auto" }}>
              {genBusy ? "生成中…" : "让 AI 再生成 12 个"}
            </button>
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th>车型</th>
                <th>电池</th>
                <th>充电曲线（CC-CV）</th>
                <th>异常概率画像</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.id}>
                  <td style={{ fontWeight: 600 }}>{p.model_name}</td>
                  <td className="mono">{p.battery_kwh} kWh</td>
                  <td style={{ width: 220 }}>
                    <CurvePreview curve={p.soc_curve} />
                  </td>
                  <td className="mono" style={{ fontSize: 12, color: "var(--text-dim)" }}>
                    降功率 {(100 * (p.anomaly_profile?.derate ?? 0)).toFixed(1)}% · 枪温{" "}
                    {(100 * (p.anomaly_profile?.guntemp ?? 0)).toFixed(1)}% · 掉线{" "}
                    {(100 * (p.anomaly_profile?.offline ?? 0)).toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "ai" && (
        <div className="card">
          <div className="grid grid-3" style={{ marginBottom: 16 }}>
            <div>
              <div className="kpi-value" style={{ fontSize: 22 }}>${aiLogs?.total_cost_usd?.toFixed(4) ?? "0"}</div>
              <div className="kpi-label">累计 AI 成本</div>
            </div>
            <div>
              <div className="kpi-value" style={{ fontSize: 22 }}>{aiLogs?.call_count ?? 0}</div>
              <div className="kpi-label">调用次数</div>
            </div>
            <div>
              <div className="kpi-value" style={{ fontSize: 22, color: "var(--green)" }}>
                {((aiLogs?.ok_rate ?? 0) * 100).toFixed(1)}%
              </div>
              <div className="kpi-label">成功率</div>
            </div>
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th>场景</th>
                <th>模型</th>
                <th>Prompt tok</th>
                <th>Completion tok</th>
                <th>缓存命中</th>
                <th>延迟</th>
                <th>成本</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {(aiLogs?.calls ?? []).slice(0, 30).map((l) => (
                <tr key={l.id}>
                  <td>{SCENE_LABEL[l.scene] ?? l.scene}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{l.model}</td>
                  <td className="mono">{l.prompt_tokens}</td>
                  <td className="mono">{l.completion_tokens}</td>
                  <td className="mono">{l.cache_hit_tokens}</td>
                  <td className="mono">{l.latency_ms}ms</td>
                  <td className="mono">${l.cost_usd.toFixed(6)}</td>
                  <td>
                    <span className={`tag ${l.ok ? "green" : "red"}`}>{l.ok ? "成功" : "失败"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "report" && (
        <div className="card">
          <div style={{ display: "flex", alignItems: "center", marginBottom: 14 }}>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
              DeepSeek 基于当日运营数据自动撰写日报（失败静默降级，不阻塞运营）
            </span>
            <button
              className="btn primary small"
              disabled={reportBusy}
              onClick={generateReport}
              style={{ marginLeft: "auto" }}
            >
              {reportBusy ? "生成中…" : "生成今日日报"}
            </button>
          </div>
          {reports.length === 0 ? (
            <div className="empty">暂无日报</div>
          ) : (
            <div style={{ display: "grid", gap: 12 }}>
              {reports.map((r) => (
                <div key={r.date} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "12px 16px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                    <span style={{ fontWeight: 600 }}>{r.date} 日报</span>
                    <span className="mono" style={{ color: "var(--text-dim)", fontSize: 12 }}>
                      {JSON.stringify(r.stats).slice(0, 120)}
                    </span>
                  </div>
                  <div style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.7, color: "#3b4a63" }}>
                    {r.narrative}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {tab === "health" && (
        <div className="card">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
              PHM 预测性维护：工单压力 + 运行状态 + 实时枪温的可解释加权评分（每 30s 自动刷新，展示健康度最低的
              {health?.piles.length ?? 12} 桩，共 {health?.total ?? 168} 桩）
            </span>
          </div>
          {!health ? (
            <div className="empty">评分计算中…</div>
          ) : health.piles.length === 0 ? (
            <div className="empty">暂无评分数据</div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>桩</th>
                  <th>站点</th>
                  <th style={{ width: "22%" }}>健康度</th>
                  <th>等级</th>
                  <th>扣分项</th>
                  <th>调度限功率</th>
                  <th>维护建议</th>
                </tr>
              </thead>
              <tbody>
                {health.piles.map((p) => {
                  const gradeCls = p.grade === "优" ? "green" : p.grade === "良" ? "cyan" : p.grade === "关注" ? "amber" : "red";
                  const barColor =
                    p.score >= 85 ? "linear-gradient(90deg,#10b981,#34d399)" : p.score >= 70 ? "linear-gradient(90deg,#06b6d4,#22d3ee)" : p.score >= 50 ? "linear-gradient(90deg,#f59e0b,#fbbf24)" : "linear-gradient(90deg,#ef4444,#f87171)";
                  return (
                    <tr key={p.pile_id}>
                      <td className="mono" style={{ fontWeight: 600 }}>{p.code}</td>
                      <td style={{ color: "var(--text-dim)" }}>{p.station}</td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ background: "#edf2f8", borderRadius: 6, height: 8, overflow: "hidden", flex: 1 }}>
                            <div style={{ height: "100%", width: `${p.score}%`, background: barColor }} />
                          </div>
                          <span className="mono" style={{ fontSize: 12, width: 30 }}>{p.score}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`tag ${gradeCls}`}>{p.grade}</span>
                      </td>
                      <td style={{ maxWidth: 260, fontSize: 12, color: "var(--text-dim)" }}>
                        {p.reasons.join(" · ")}
                      </td>
                      <td className="mono">
                        {p.limit_kw == null ? "–" : `${p.limit_kw} kW`}
                      </td>
                      <td style={{ fontSize: 12.5 }}>{p.suggestion}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
