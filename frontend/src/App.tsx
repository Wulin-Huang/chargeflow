import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./store";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Stations from "./pages/Stations";
import Charging from "./pages/Charging";
import History from "./pages/History";
import Assistant from "./pages/Assistant";
import Dashboard from "./pages/Dashboard";
import Admin from "./pages/Admin";
import ChargingMap from "./pages/ChargingMap";
import Vehicles from "./pages/Vehicles";

function RequireRole({ roles, children }: { roles: string[]; children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="empty">加载中…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (!roles.includes(user.role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Stations />} />
        <Route path="/map" element={<ChargingMap />} />
        <Route path="/charging" element={<Charging />} />
        <Route path="/history" element={<History />} />
        <Route path="/assistant" element={<Assistant />} />
        <Route path="/vehicles" element={<Vehicles />} />
        <Route
          path="/dashboard"
          element={
            <RequireRole roles={["operator", "admin"]}>
              <Dashboard />
            </RequireRole>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireRole roles={["admin"]}>
              <Admin />
            </RequireRole>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
