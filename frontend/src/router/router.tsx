import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from "@tanstack/react-router";

import { fetchCurrentUser, USER_HOME_PATH } from "../auth/auth";
import type { CurrentUser } from "../auth/auth";
import { AppShell } from "../shell/AppShell";
import { LoginPage } from "../pages/LoginPage";
import { DashboardPage } from "../pages/DashboardPage";
import { ExtensionsPage } from "../pages/ExtensionsPage";
import { TrunksPage } from "../pages/TrunksPage";
import { RoutesPage } from "../pages/RoutesPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { AiAgentsPage } from "../pages/AiAgentsPage";
import { ContactsPage } from "../pages/ContactsPage";
import { CdrPage } from "../pages/CdrPage";
import { WorkflowsPage } from "../pages/WorkflowsPage";
import { WorkflowEditorPage } from "../pages/WorkflowEditorPage";
import { NetworkPage } from "../pages/NetworkPage";
import { NetworkRemotePage } from "../pages/NetworkRemotePage";
import { DevicesPage } from "../pages/DevicesPage";
import { UsersPage } from "../pages/UsersPage";
import { SecurityPage } from "../pages/SecurityPage";
import { SystemPage } from "../pages/SystemPage";
import { AuditPage } from "../pages/AuditPage";
import { SsoPage } from "../pages/SsoPage";
import { SettingsPage } from "../pages/SettingsPage";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

/**
 * 認証済み領域のレイアウトルート。
 * /login 以外へのアクセス時、GET /api/auth/me で認証を確認し、
 * 未認証なら /login へリダイレクトする（横断ガード）。
 */
const authLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "authenticated",
  beforeLoad: async (): Promise<{ user: CurrentUser }> => {
    const user = await fetchCurrentUser();
    if (!user) {
      throw redirect({ to: "/login" });
    }
    return { user };
  },
  component: AuthenticatedLayout,
});

function AuthenticatedLayout() {
  const { user } = authLayoutRoute.useRouteContext();
  return <AppShell username={user.display_name || user.username} role={user.role} />;
}

/**
 * admin 専用領域のレイアウトルート（パスなし）。
 * 直接 URL を叩かれた場合も、admin 以外はアカウントページへリダイレクトする。
 * バックエンド API は require_admin で保護済みだが、UI 側でも 403 画面を見せない。
 */
const adminLayoutRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  id: "admin",
  beforeLoad: ({ context }) => {
    if (context.user.role !== "admin") {
      throw redirect({ to: USER_HOME_PATH });
    }
  },
});

const dashboardRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/",
  component: DashboardPage,
});

const extensionsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/extensions",
  component: ExtensionsPage,
});

const trunksRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/trunks",
  component: TrunksPage,
});

const routesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/routes",
  component: RoutesPage,
});

const providersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/providers",
  component: ProvidersPage,
});

const aiAgentsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai-agents",
  component: AiAgentsPage,
});

const contactsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/contacts",
  component: ContactsPage,
});

const cdrRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/cdr",
  component: CdrPage,
});

const workflowsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/workflows",
  component: WorkflowsPage,
});

const workflowEditorRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/workflows/$workflowId",
  component: WorkflowEditorPage,
});

// ネットワーク（内向き）: 電話管理用 LAN 側（LAN / DHCP / NAT）。
// 従来の /network パスを互換のため維持する。
const networkRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/network",
  component: NetworkPage,
});

// ネットワーク（外向き）: リモートアクセス側（Tailscale など）。
const networkRemoteRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/network/remote",
  component: NetworkRemotePage,
});

const devicesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/devices",
  component: DevicesPage,
});

// ─── Phase 6 認証強化ページ（T9b） ───

const usersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/users",
  component: UsersPage,
});

// セキュリティ（2FA 自己設定）は一般ユーザーにも開放するため
// admin レイアウトの外（認証済みレイアウト直下）に置く。
const securityRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/settings/security",
  component: SecurityPage,
});

const systemRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/system",
  component: SystemPage,
});

const auditRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/audit",
  component: AuditPage,
});

const ssoRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/sso",
  component: SsoPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/settings",
  component: SettingsPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  authLayoutRoute.addChildren([
    securityRoute,
    adminLayoutRoute.addChildren([
      dashboardRoute,
      extensionsRoute,
      trunksRoute,
      routesRoute,
      providersRoute,
      aiAgentsRoute,
      contactsRoute,
      cdrRoute,
      workflowsRoute,
      workflowEditorRoute,
      networkRoute,
      networkRemoteRoute,
      devicesRoute,
      usersRoute,
      systemRoute,
      auditRoute,
      ssoRoute,
      settingsRoute,
    ]),
  ]),
]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
