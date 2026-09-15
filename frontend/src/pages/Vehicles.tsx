import { useEffect, useState } from "react";
import { Car, Plus, Battery, Target, Trash2, Star, StarOff } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../store";

interface Vehicle {
  id: number;
  nickname: string;
  plate: string;
  battery_kwh: number;
  default_target_soc: number;
  is_default: boolean;
  profile_id: number | null;
  profile_name: string | null;
  total_sessions: number;
  total_kwh: number;
}

interface Profile {
  id: number;
  model_name: string;
  battery_kwh: number;
}

export default function Vehicles() {
  useAuth();
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Vehicle | null>(null);
  const [form, setForm] = useState({
    nickname: "",
    plate: "",
    battery_kwh: 60,
    default_target_soc: 80,
    profile_id: "" as string,
  });
  const [toast, setToast] = useState("");

  const load = async () => {
    const r = await api.get<{ total: number; vehicles: Vehicle[] }>("/vehicles").catch(() => null);
    if (r) setVehicles(r.vehicles);
  };

  useEffect(() => {
    load();
    api.get<Profile[]>("/admin/vehicle-profiles")
      .then(setProfiles)
      .catch(() => {});
  }, []);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(""), 2400);
  };

  const openAdd = () => {
    setEditing(null);
    setForm({ nickname: "", plate: "", battery_kwh: 60, default_target_soc: 80, profile_id: "" });
    setShowForm(true);
  };

  const openEdit = (v: Vehicle) => {
    setEditing(v);
    setForm({
      nickname: v.nickname,
      plate: v.plate,
      battery_kwh: v.battery_kwh,
      default_target_soc: v.default_target_soc,
      profile_id: v.profile_id ? String(v.profile_id) : "",
    });
    setShowForm(true);
  };

  const submit = async () => {
    if (!form.nickname.trim()) {
      flash("请输入车辆昵称");
      return;
    }
    const body = {
      nickname: form.nickname,
      plate: form.plate,
      battery_kwh: Number(form.battery_kwh),
      default_target_soc: Number(form.default_target_soc),
      profile_id: form.profile_id ? Number(form.profile_id) : null,
    };
    try {
      if (editing) {
        await api.patch(`/vehicles/${editing.id}`, body);
        flash("车辆信息已更新");
      } else {
        await api.post("/vehicles", body);
        flash("车辆已添加");
      }
      setShowForm(false);
      load();
    } catch (err: any) {
      flash(err?.response?.data?.detail || "保存失败");
    }
  };

  const setDefault = async (v: Vehicle) => {
    await api.patch(`/vehicles/${v.id}`, { is_default: true });
    flash(`${v.nickname} 已设为默认车辆`);
    load();
  };

  const remove = async (v: Vehicle) => {
    if (!confirm(`确定删除「${v.nickname}」吗？`)) return;
    try {
      await api.del(`/vehicles/${v.id}`);
      flash("已删除");
      load();
    } catch (err: any) {
      flash(err?.response?.data?.detail || "删除失败");
    }
  };

  return (
    <div>
      <div className="page-head">
        <span className="page-title">我的爱车</span>
        <span className="page-sub">
          管理您的车辆，设置默认充电目标，关联车型参数以获得更精准的充电时长预估
        </span>
        <button className="btn primary" onClick={openAdd} style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4 }}>
          <Plus size={14} /> 添加车辆
        </button>
      </div>
      {toast && <div className="toast">{toast}</div>}

      {vehicles.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "48px 20px" }}>
          <Car size={48} color="var(--text-dim)" style={{ marginBottom: 12 }} />
          <div style={{ color: "var(--text-dim)", marginBottom: 16 }}>还没有添加车辆，添加您的爱车吧</div>
          <button className="btn primary" onClick={openAdd}>
            <Plus size={14} style={{ marginRight: 4 }} /> 添加第一辆车
          </button>
        </div>
      ) : (
        <div className="grid grid-2">
          {vehicles.map((v) => (
            <div key={v.id} className="card" style={{ position: "relative" }}>
              {v.is_default && (
                <span className="tag amber" style={{ position: "absolute", top: 14, right: 14 }}>默认</span>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
                <div
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 12,
                    background: "linear-gradient(135deg,#3b82f6,#06b6d4)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "white",
                  }}
                >
                  <Car size={24} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 16, fontWeight: 600 }}>{v.nickname}</div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                    {v.plate || "未设置车牌"} {v.profile_name && `· ${v.profile_name}`}
                  </div>
                </div>
              </div>

              <div className="grid grid-3" style={{ marginBottom: 12 }}>
                <div>
                  <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 2 }}>
                    <Battery size={11} style={{ verticalAlign: -1, marginRight: 3 }} />
                    电池容量
                  </div>
                  <div className="mono" style={{ fontWeight: 600 }}>{v.battery_kwh} kWh</div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 2 }}>
                    <Target size={11} style={{ verticalAlign: -1, marginRight: 3 }} />
                    默认目标
                  </div>
                  <div className="mono" style={{ fontWeight: 600 }}>{v.default_target_soc}%</div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 2 }}>累计充电</div>
                  <div className="mono" style={{ fontWeight: 600 }}>
                    {v.total_sessions}次 · {v.total_kwh}度
                  </div>
                </div>
              </div>

              <div style={{ display: "flex", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                <button className="btn small" onClick={() => openEdit(v)} style={{ flex: 1 }}>
                  编辑
                </button>
                {!v.is_default && (
                  <button className="btn small" onClick={() => setDefault(v)} style={{ flex: 1 }}>
                    <StarOff size={12} style={{ marginRight: 3 }} /> 设为默认
                  </button>
                )}
                {v.is_default && (
                  <button className="btn small" disabled style={{ flex: 1, opacity: 0.6 }}>
                    <Star size={12} style={{ marginRight: 3 }} /> 默认车辆
                  </button>
                )}
                <button className="btn small" onClick={() => remove(v)} style={{ color: "var(--red)" }}>
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <div className="modal-mask" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 420 }}>
            <div className="modal-title">{editing ? "编辑车辆" : "添加车辆"}</div>

            <div style={{ display: "grid", gap: 12 }}>
              <div>
                <label style={{ fontSize: 12, color: "var(--text-dim)", display: "block", marginBottom: 4 }}>
                  车辆昵称
                </label>
                <input
                  className="input"
                  value={form.nickname}
                  onChange={(e) => setForm({ ...form, nickname: e.target.value })}
                  placeholder="如：我的小白"
                />
              </div>
              <div>
                <label style={{ fontSize: 12, color: "var(--text-dim)", display: "block", marginBottom: 4 }}>
                  车牌号（选填）
                </label>
                <input
                  className="input"
                  value={form.plate}
                  onChange={(e) => setForm({ ...form, plate: e.target.value.toUpperCase() })}
                  placeholder="粤A12345"
                />
              </div>
              <div className="grid grid-2">
                <div>
                  <label style={{ fontSize: 12, color: "var(--text-dim)", display: "block", marginBottom: 4 }}>
                    电池容量（kWh）
                  </label>
                  <input
                    className="input"
                    type="number"
                    value={form.battery_kwh}
                    onChange={(e) => setForm({ ...form, battery_kwh: Number(e.target.value) })}
                  />
                </div>
                <div>
                  <label style={{ fontSize: 12, color: "var(--text-dim)", display: "block", marginBottom: 4 }}>
                    默认充电目标 SOC
                  </label>
                  <input
                    className="input"
                    type="number"
                    min={20}
                    max={100}
                    value={form.default_target_soc}
                    onChange={(e) => setForm({ ...form, default_target_soc: Number(e.target.value) })}
                  />
                </div>
              </div>
              <div>
                <label style={{ fontSize: 12, color: "var(--text-dim)", display: "block", marginBottom: 4 }}>
                  关联车型参数包（选填，用于精准预估充电时长）
                </label>
                <select
                  className="select"
                  value={form.profile_id}
                  onChange={(e) => setForm({ ...form, profile_id: e.target.value })}
                >
                  <option value="">不关联</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.model_name}（{p.battery_kwh}kWh）
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
              <button className="btn small" onClick={() => setShowForm(false)}>
                取消
              </button>
              <button className="btn small primary" onClick={submit}>
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
