import { useEffect, useState } from "react";
import { api } from "../api";

interface SessionOut {
  id: number;
  pile_id: number;
  status: string;
  start_at: string | null;
  end_at: string | null;
  kwh_wh: number;
  stop_reason: string;
}

interface Bill {
  settled: boolean;
  total_cents?: number;
  est_total_cents?: number;
  energy_fee_cents?: number;
  service_fee_cents?: number;
  occupy_fee_cents?: number;
  segments?: { start: string; end: string; kwh: number; price_cents: number; fee_cents: number }[];
}

const STATUS_TAG: Record<string, string> = {
  charging: "cyan",
  starting: "cyan",
  occupied: "amber",
  settled: "green",
  failed: "red",
};

export default function History() {
  const [rows, setRows] = useState<SessionOut[]>([]);
  const [bills, setBills] = useState<Record<number, Bill>>({});
  const [detail, setDetail] = useState<{ session: SessionOut; bill: Bill | null } | null>(null);

  useEffect(() => {
    api.get<SessionOut[]>("/sessions/history").then(async (sessions) => {
      setRows(sessions);
      const settled = sessions.filter((s) => s.status === "settled");
      const results = await Promise.all(
        settled.map((s) => api.get<Bill>(`/sessions/${s.id}/bill`).catch(() => null))
      );
      const map: Record<number, Bill> = {};
      settled.forEach((s, i) => {
        if (results[i]) map[s.id] = results[i];
      });
      setBills(map);
    });
  }, []);

  return (
    <div>
      <div className="page-head">
        <span className="page-title">订单记录</span>
        <span className="page-sub">分时计费账单由遥测明细分段积分生成</span>
      </div>
      <div className="card">
        {rows.length === 0 ? (
          <div className="empty">暂无充电记录</div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>会话</th>
                <th>桩</th>
                <th>开始时间</th>
                <th>状态</th>
                <th>电量</th>
                <th>金额</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const bill = bills[s.id];
                const yuan = bill
                  ? ((bill.settled ? bill.total_cents : bill.est_total_cents) ?? 0) / 100
                  : null;
                return (
                  <tr key={s.id}>
                    <td className="mono">#{s.id}</td>
                    <td className="mono">P{String(s.pile_id).padStart(3, "0")}</td>
                    <td>
                      {s.start_at ? new Date(s.start_at).toLocaleString("zh-CN", { hour12: false }) : "-"}
                    </td>
                    <td>
                      <span className={`tag ${STATUS_TAG[s.status] ?? "gray"}`}>
                        {s.status}
                        {s.stop_reason ? ` · ${s.stop_reason}` : ""}
                      </span>
                    </td>
                    <td className="mono">{(s.kwh_wh / 1000).toFixed(2)} kWh</td>
                    <td className="mono">{yuan !== null ? `¥${yuan.toFixed(2)}` : "-"}</td>
                    <td>
                      {bill && (
                        <button className="btn small" onClick={() => setDetail({ session: s, bill })}>
                          账单
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {detail && (
        <div className="modal-mask" onClick={() => setDetail(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">
              会话 #{detail.session.id} 分时账单
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>时段</th>
                  <th>电量</th>
                  <th>单价</th>
                  <th>电费</th>
                </tr>
              </thead>
              <tbody>
                {(detail.bill?.segments ?? []).map((seg, i) => (
                  <tr key={i}>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {seg.start.slice(11, 16)} - {seg.end.slice(11, 16)}
                    </td>
                    <td className="mono">{seg.kwh.toFixed(3)} kWh</td>
                    <td className="mono">¥{(seg.price_cents / 100).toFixed(2)}/kWh</td>
                    <td className="mono">¥{(seg.fee_cents / 100).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ display: "grid", gap: 6, marginTop: 14, fontSize: 13.5 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-dim)" }}>电费</span>
                <span className="mono">¥{((detail.bill?.energy_fee_cents ?? 0) / 100).toFixed(2)}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-dim)" }}>服务费</span>
                <span className="mono">¥{((detail.bill?.service_fee_cents ?? 0) / 100).toFixed(2)}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-dim)" }}>占位费</span>
                <span className="mono">¥{((detail.bill?.occupy_fee_cents ?? 0) / 100).toFixed(2)}</span>
              </div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  borderTop: "1px solid var(--border)",
                  paddingTop: 8,
                  fontWeight: 700,
                  color: "var(--green)",
                }}
              >
                <span>合计</span>
                <span className="mono">¥{((detail.bill?.total_cents ?? 0) / 100).toFixed(2)}</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
