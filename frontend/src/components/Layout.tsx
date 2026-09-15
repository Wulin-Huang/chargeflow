import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth, useLive, useLiveEvents } from "../store";
import { useEffect, useState } from "react";
import { Zap, Plug, MapPin, Activity, ReceiptText, Bot, Car, LayoutDashboard, SlidersHorizontal } from "lucide-react";

const ROLE_LABEL: Record<string, string> = {
  customer: "车主用户",
  operator: "运维员",
  admin: "管理员",
};

export default function Layout() {
  const { user, loading, logout } = useAuth();
  const { connected } = useLive();
  const navigate = useNavigate();
  const location = useLocation();
  const [woCount, setWoCount] = useState(0);

  useLiveEvents(["work_order"], () => setWoCount((c) => c + 1));
  useEffect(() => {
    if (location.pathname === "/admin") setWoCount(0);
  }, [location.pathname]);

  useEffect(() => {
    if (!loading && !user) navigate("/login", { replace: true });
  }, [loading, user, navigate]);

  if (loading || !user) return null;

  const isStaff = user.role === "operator" || user.role === "admin";

  return (
    <div className="shell">
      <aside className="sidenav">
        <div className="brand">
          <div className="brand-logo">
            <Zap size={19} strokeWidth={2.4} fill="currentColor" />
          </div>
          <div>
            <div className="brand-name">ChargeFlow</div>
            <div className="brand-sub">充电站 IoT 运营平台</div>
          </div>
        </div>
        <nav className="nav-group">
          <div className="nav-label">车主服务</div>
          <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <Plug size={16} /> 站点与充电
          </NavLink>
          <NavLink to="/map" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <MapPin size={16} /> 充电地图
          </NavLink>
          <NavLink to="/charging" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <Activity size={16} /> 充电监控
          </NavLink>
          <NavLink to="/history" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <ReceiptText size={16} /> 订单记录
          </NavLink>
          <NavLink to="/assistant" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <Bot size={16} /> AI 助手
          </NavLink>
          <NavLink to="/vehicles" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <Car size={16} /> 我的爱车
          </NavLink>
          {isStaff && (
            <>
              <div className="nav-label">运营管理</div>
              <NavLink to="/dashboard" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
                <LayoutDashboard size={16} /> 运营大屏
              </NavLink>
              {user.role === "admin" && (
                <NavLink to="/admin" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
                  <SlidersHorizontal size={16} /> 平台管理
                  {woCount > 0 && <span className="nav-badge">{woCount}</span>}
                </NavLink>
              )}
            </>
          )}
        </nav>
        <div className="user-box">
          <div className="user-name">{user.nickname}</div>
          <div className="user-role">
            {ROLE_LABEL[user.role]} ·{" "}
            <span style={{ color: connected ? "var(--green)" : "var(--text-dim)" }}>
              <span className={`dot ${connected ? "green" : "red"}`} style={{ marginRight: 4 }} />
              实时
            </span>
          </div>
          {user.role === "customer" && (
            <div className="user-balance">余额 ¥{(user.balance_cents / 100).toFixed(2)}</div>
          )}
          <button className="btn small" style={{ marginTop: 10 }} onClick={() => { logout(); }}>
            退出登录
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
