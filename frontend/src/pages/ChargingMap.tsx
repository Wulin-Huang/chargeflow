import { useEffect, useMemo, useState } from "react";
import { MapPin, Zap, Navigation, Search, ChevronRight } from "lucide-react";
import { api } from "../api";
import EChart, { TOOLTIP } from "../components/EChart";
import { useNavigate } from "react-router-dom";

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

// 用户位置（模拟广州珠江新城）
const USER_LOC = { lat: 23.12, lng: 113.32 };

function haversine(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export default function ChargingMap() {
  const navigate = useNavigate();
  const [stations, setStations] = useState<Station[]>([]);
  const [keyword, setKeyword] = useState("");
  const [filterFree, setFilterFree] = useState(false);

  useEffect(() => {
    api.get<Station[]>("/stations").then(setStations).catch(() => {});
  }, []);

  const withDistance = useMemo(() => {
    return stations.map((s) => ({
      ...s,
      distance_km: haversine(USER_LOC.lat, USER_LOC.lng, s.lat, s.lng),
    }));
  }, [stations]);

  const filtered = useMemo(() => {
    let list = withDistance;
    if (keyword.trim()) {
      const k = keyword.trim().toLowerCase();
      list = list.filter((s) => s.name.toLowerCase().includes(k) || s.address.toLowerCase().includes(k));
    }
    if (filterFree) {
      list = list.filter((s) => s.free_piles > 0);
    }
    return [...list].sort((a, b) => a.distance_km - b.distance_km);
  }, [withDistance, keyword, filterFree]);

  const mapOption = useMemo(() => {
    if (!withDistance.length) return {};
    const lats = withDistance.map((s) => s.lat);
    const lngs = withDistance.map((s) => s.lng);
    const latMin = Math.min(...lats) - 0.3;
    const latMax = Math.max(...lats) + 0.3;
    const lngMin = Math.min(...lngs) - 0.3;
    const lngMax = Math.max(...lngs) + 0.3;

    return {
      grid: { left: 0, right: 0, top: 0, bottom: 0 },
      tooltip: {
        ...TOOLTIP,
        formatter: (params: any) => {
          const d = params.data;
          return `
            <div style="font-weight:600;margin-bottom:4px">${d.name}</div>
            <div style="font-size:12px;color:#62708c;line-height:1.6">
              ${d[3]}<br/>
              ${d[4]} 空闲 / ${d[5]} 总桩<br/>
              距离 ${d[6]} km
            </div>
          `;
        },
      },
      xAxis: {
        type: "value" as const,
        min: lngMin,
        max: lngMax,
        show: false,
      },
      yAxis: {
        type: "value" as const,
        min: latMin,
        max: latMax,
        show: false,
      },
      series: [
        {
          type: "scatter" as const,
          symbolSize: (val: number[]) => {
            const total = val[5];
            return Math.max(10, Math.min(28, 8 + total * 0.8));
          },
          itemStyle: {
            color: (params: any) => {
              const free = params.data[4];
              const total = params.data[5];
              if (free === 0) return "#ef4444";
              if (free / total > 0.4) return "#10b981";
              return "#f59e0b";
            },
            opacity: 0.85,
            borderColor: "#fff",
            borderWidth: 2,
          },
          label: {
            show: true,
            position: "top" as const,
            formatter: (p: any) => p.data.name.replace(/^.*?·/, ""),
            fontSize: 10,
            color: "#3b4a63",
          },
          data: withDistance.map((s) => ({
            name: s.name,
            value: [s.lng, s.lat, s.id, s.address, s.free_piles, s.total_piles, s.distance_km.toFixed(1)],
          })),
        },
        {
          type: "scatter" as const,
          symbol: "pin",
          symbolSize: 36,
          itemStyle: { color: "#3b82f6" },
          label: {
            show: true,
            color: "#fff",
            fontSize: 11,
            formatter: "我",
          },
          data: [[USER_LOC.lng, USER_LOC.lat]],
        },
      ],
    };
  }, [withDistance]);

  const goStation = (id: number) => {
    navigate(`/?station=${id}`);
  };

  return (
    <div>
      <div className="page-head">
        <span className="page-title">充电地图</span>
        <span className="page-sub">
          <MapPin size={12} style={{ verticalAlign: -1, marginRight: 3 }} />
          附近 {filtered.length} 个充电站 · 按距离由近到远
        </span>
      </div>

      <div className="card" style={{ marginBottom: 14, padding: 12 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
          <div style={{ position: "relative", flex: 1 }}>
            <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-dim)" }} />
            <input
              className="input"
              placeholder="搜索站点名称或地址"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              style={{ paddingLeft: 32 }}
            />
          </div>
          <button
            className={`btn small${filterFree ? " primary" : ""}`}
            onClick={() => setFilterFree(!filterFree)}
            style={{ whiteSpace: "nowrap" }}
          >
            <Zap size={13} style={{ marginRight: 3 }} /> 只看有空桩
          </button>
        </div>
        <div style={{ height: 280, background: "linear-gradient(180deg,#e0f2fe,#f0fdf4)", borderRadius: 10 }}>
          {withDistance.length > 0 && <EChart option={mapOption} height={280} />}
        </div>
        <div style={{ display: "flex", gap: 16, justifyContent: "center", fontSize: 12, color: "var(--text-dim)", marginTop: 8 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#10b981" }} /> 空闲充足
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#f59e0b" }} /> 紧张
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#ef4444" }} /> 无空闲
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#3b82f6" }} /> 我的位置
          </span>
        </div>
      </div>

      <div className="card">
        <div className="card-title">站点列表（按距离排序）</div>
        <table className="tbl">
          <thead>
            <tr>
              <th>站点</th>
              <th>距离</th>
              <th>空闲 / 总桩</th>
              <th>电价</th>
              <th style={{ width: 80 }}></th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 20).map((s) => (
              <tr key={s.id}>
                <td>
                  <div style={{ fontWeight: 600 }}>{s.name}</div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{s.address}</div>
                </td>
                <td className="mono">
                  <Navigation size={12} style={{ verticalAlign: -1, marginRight: 3, color: "var(--accent)" }} />
                  {s.distance_km < 1 ? `${(s.distance_km * 1000).toFixed(0)} m` : `${s.distance_km.toFixed(1)} km`}
                </td>
                <td>
                  <span style={{ color: s.free_piles > 0 ? "var(--green)" : "var(--red)", fontWeight: 600 }}>
                    {s.free_piles}
                  </span>
                  <span style={{ color: "var(--text-dim)" }}> / {s.total_piles}</span>
                </td>
                <td style={{ fontSize: 12, color: "var(--text-dim)" }}>{s.price_hint}</td>
                <td>
                  <button className="btn small" onClick={() => goStation(s.id)}>
                    查看 <ChevronRight size={13} style={{ verticalAlign: -2 }} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && <div className="empty">没有匹配的站点</div>}
      </div>
    </div>
  );
}
