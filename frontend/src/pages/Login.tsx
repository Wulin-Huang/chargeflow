import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../store";
import { Zap } from "lucide-react";

const DEMO_ACCOUNTS = [
  { phone: "13800000001", password: "customer123", label: "车主用户" },
  { phone: "13800000002", password: "operator123", label: "运维员" },
  { phone: "13800000000", password: "admin123", label: "管理员" },
];

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [phone, setPhone] = useState("13800000001");
  const [password, setPassword] = useState("customer123");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(phone, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div style={{ width: 380 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, justifyContent: "center", marginBottom: 28 }}>
          <div className="brand-logo" style={{ width: 46, height: 46, borderRadius: 12 }}>
            <Zap size={26} strokeWidth={2.2} fill="currentColor" />
          </div>
          <div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>ChargeFlow</div>
            <div style={{ color: "var(--text-dim)", fontSize: 12.5 }}>充电站 IoT 运营平台 · 实时数据驱动</div>
          </div>
        </div>
        <form className="card" onSubmit={submit} style={{ display: "grid", gap: 12 }}>
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 6 }}>手机号</div>
            <input
              className="input"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="手机号"
              autoFocus
            />
          </div>
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 6 }}>密码</div>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="密码"
            />
          </div>
          {error && <div style={{ color: "var(--red)", fontSize: 13 }}>{error}</div>}
          <button className="btn primary" disabled={busy} type="submit">
            {busy ? "登录中…" : "登 录"}
          </button>
        </form>
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-title">演示账号（点击填充）</div>
          <div style={{ display: "grid", gap: 8 }}>
            {DEMO_ACCOUNTS.map((a) => (
              <button
                key={a.phone}
                className="btn small"
                style={{ textAlign: "left" }}
                onClick={() => {
                  setPhone(a.phone);
                  setPassword(a.password);
                }}
              >
                {a.label} · {a.phone}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
