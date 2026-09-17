import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider } from "@/contexts/AuthContext";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { AppShell } from "@/components/layout/AppShell";
import Login from "@/pages/Login";
import AcceptInvite from "@/pages/AcceptInvite";
import Dashboard from "@/pages/Dashboard";
import Settings from "@/pages/Settings";
import NotificationsPage from "@/pages/Notifications";
import ActivityLogs from "@/pages/ActivityLogs";
import AboutWavygo from "@/pages/AboutWavygo";
import ModulePlaceholder from "@/pages/ModulePlaceholder";
import Marketplace from "@/pages/Marketplace";
import TaskBoard from "@/pages/TaskBoard";
import Employees from "@/pages/Employees";
import OpportunityHub from "@/pages/OpportunityHub";
import WavygoConnect from "@/pages/WavygoConnect";

const PLACEHOLDER_ROUTES = [
  "/company-vault", "/finance", "/crm",
  "/marketing", "/analytics", "/calendar", "/wavygo-ai",
];

function Shell({ children, module }) {
  return (
    <ProtectedRoute allowedModule={module}>
      <AppShell>{children}</AppShell>
    </ProtectedRoute>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/login" element={<Login />} />
            <Route path="/accept-invite" element={<AcceptInvite />} />
            <Route path="/dashboard"       element={<Shell module="dashboard"><Dashboard /></Shell>} />
            <Route path="/marketplace"     element={<Shell module="marketplace"><Marketplace /></Shell>} />
            <Route path="/task-board"      element={<Shell module="task-board"><TaskBoard /></Shell>} />
            <Route path="/employees"       element={<Shell module="employees"><Employees /></Shell>} />
            <Route path="/opportunity-hub" element={<Shell module="opportunity-hub"><OpportunityHub /></Shell>} />
            <Route path="/wavygo-connect"  element={<Shell module="wavygo-connect"><WavygoConnect /></Shell>} />
            <Route path="/settings"        element={<Shell module="settings"><Settings /></Shell>} />
            <Route path="/notifications"   element={<Shell module="notifications"><NotificationsPage /></Shell>} />
            <Route path="/activity-logs"   element={<Shell module="activity-logs"><ActivityLogs /></Shell>} />
            <Route path="/about-wavygo"    element={<Shell module="about-wavygo"><AboutWavygo /></Shell>} />
            {PLACEHOLDER_ROUTES.map((p) => (
              <Route key={p} path={p} element={<Shell module={p.slice(1)}><ModulePlaceholder /></Shell>} />
            ))}
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
          <Toaster richColors position="bottom-right" />
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  );
}
