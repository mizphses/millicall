// @vitest-environment jsdom
/**
 * ルート単位の遅延ロード（Issue #82）のテスト。
 *
 * - 認証ガード（beforeLoad）が遅延チャンクの外に残り、未認証ではページチャンクを取得しない
 * - ページは初回アクセス時に初めて評価される（静的 import されていない）
 * - チャンク取得失敗時に日本語エラーと再読み込み導線が表示される
 */
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const loaded = vi.hoisted(() => ({
  modules: [] as string[],
}));

vi.mock("../auth/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../auth/auth")>()),
  fetchCurrentUser: vi.fn(),
  fetchLoginConfig: vi.fn(async () => ({ totp_required: false })),
}));

// ページ本体はモジュール評価時に記録だけ行う軽量スタブへ差し替える
vi.mock("../pages/LoginPage", () => {
  loaded.modules.push("LoginPage");
  return { LoginPage: () => <div>login-page</div> };
});
vi.mock("../pages/DashboardPage", () => {
  loaded.modules.push("DashboardPage");
  return { DashboardPage: () => <div>dashboard-page</div> };
});
vi.mock("../pages/WorkflowEditorPage", () => {
  loaded.modules.push("WorkflowEditorPage");
  return { WorkflowEditorPage: () => <div>editor-page</div> };
});
vi.mock("../pages/SecurityPage", () => {
  loaded.modules.push("SecurityPage");
  return { SecurityPage: () => <div>security-page</div> };
});
vi.mock("../shell/AppShell", async () => {
  const { Outlet } = await import("@tanstack/react-router");
  loaded.modules.push("AppShell");
  return { AppShell: () => <Outlet /> };
});

import { RouteError, RoutePending, isChunkLoadError } from "../router/RouteStatus";

type CurrentUser = Awaited<ReturnType<typeof import("../auth/auth").fetchCurrentUser>>;

// lazyRouteComponent はモジュールスコープでチャンクをキャッシュするため、
// テストごとにモジュールを読み直して「初回アクセス」の状態を再現する。
// React を含む全モジュールを同じ世代で読み直すため、依存はすべて動的 import にする。
async function loadFresh() {
  vi.resetModules();
  const auth = await import("../auth/auth");
  const mockedFetchCurrentUser = vi.mocked(auth.fetchCurrentUser);
  const { RouterProvider, createMemoryHistory } = await import("@tanstack/react-router");
  const { act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { createAppRouter } = await import("../router/router");
  const { createElement } = await import("react");
  return {
    mockedFetchCurrentUser,
    RouterProvider,
    createMemoryHistory,
    act,
    createRoot,
    createAppRouter,
    createElement,
  };
}

const adminUser = {
  id: 1,
  username: "admin",
  display_name: "",
  role: "admin",
  totp_enabled: true,
} as CurrentUser;

async function mountAt(path: string, user: CurrentUser) {
  const f = await loadFresh();
  f.mockedFetchCurrentUser.mockResolvedValue(user);
  const router = f.createAppRouter(f.createMemoryHistory({ initialEntries: [path] }));
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = f.createRoot(container);
  await f.act(async () => {
    root.render(f.createElement(f.RouterProvider, { router }));
  });
  // 遅延チャンクの解決を待つ
  await f.act(async () => {
    await new Promise((r) => setTimeout(r, 50));
  });
  return { router, container, f, unmount: () => f.act(() => root.unmount()) };
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  loaded.modules.length = 0;
  vi.spyOn(console, "error").mockImplementation(() => {});
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("ルートの遅延ロード", () => {
  it("router モジュールの読み込みだけではページ本体を評価しない", async () => {
    await loadFresh();
    expect(loaded.modules).toEqual([]);
  });

  it("/login の直接アクセスでは LoginPage のみ取得し、保護ページは取得しない", async () => {
    const { container, router, f, unmount } = await mountAt("/login", null);
    expect(router.state.location.pathname).toBe("/login");
    expect(container.textContent).toContain("login-page");
    expect(loaded.modules).toEqual(["LoginPage"]);
    expect(f.mockedFetchCurrentUser).not.toHaveBeenCalled();
    await unmount();
  });

  it("未認証で保護ページへ直接アクセスすると /login へ飛び、保護ページのチャンクは取得しない", async () => {
    const { container, router, unmount } = await mountAt("/workflows/1", null);
    expect(router.state.location.pathname).toBe("/login");
    expect(container.textContent).toContain("login-page");
    expect(loaded.modules).not.toContain("WorkflowEditorPage");
    expect(loaded.modules).not.toContain("DashboardPage");
    expect(loaded.modules).not.toContain("AppShell");
    await unmount();
  });

  it("認証済み admin は直接アクセスしたルートのチャンクだけ取得する", async () => {
    const { container, router, unmount } = await mountAt("/", adminUser);
    expect(router.state.location.pathname).toBe("/");
    expect(container.textContent).toContain("dashboard-page");
    expect(loaded.modules).toContain("DashboardPage");
    expect(loaded.modules).not.toContain("WorkflowEditorPage");
    await unmount();
  });

  it("非 admin は admin 専用ページのチャンクを取得せずアカウントページへリダイレクトされる", async () => {
    const { container, router, unmount } = await mountAt("/", {
      ...adminUser,
      role: "user",
    } as CurrentUser);
    expect(router.state.location.pathname).toBe("/settings/security");
    expect(container.textContent).toContain("security-page");
    expect(loaded.modules).not.toContain("DashboardPage");
    await unmount();
  });

  it("チャンク取得に失敗したルートで日本語のエラーと再読み込み導線を表示する（本文は vitest が例外をラップするため一般エラー文言）", async () => {
    vi.doMock("../pages/DashboardPage", () => {
      throw new Error("Failed to fetch dynamically imported module: /assets/DashboardPage.js");
    });
    // 古い index.html によるリロードループ防止ガードが効かない状況を避けるため
    // モジュール未検出の自動リロードは抑止しておく
    sessionStorage.setItem(
      "tanstack_router_reload:Failed to fetch dynamically imported module: /assets/DashboardPage.js",
      "1",
    );
    const { container, unmount } = await mountAt("/", adminUser);
    expect(container.textContent).toContain("予期せぬエラーが発生しました");
    expect(container.textContent).toContain("ページを再読み込み");
    await unmount();
  });
});

describe("RouteStatus", () => {
  it("isChunkLoadError は動的 import 失敗のメッセージを判定する", () => {
    expect(isChunkLoadError(new Error("Failed to fetch dynamically imported module: x"))).toBe(
      true,
    );
    expect(isChunkLoadError(new Error("error loading dynamically imported module"))).toBe(true);
    expect(isChunkLoadError(new Error("Importing a module script failed."))).toBe(true);
    expect(isChunkLoadError(new Error("boom"))).toBe(false);
    expect(isChunkLoadError("string")).toBe(false);
  });

  it("RouteError はチャンク取得失敗時に専用の文言と再読み込みボタンを出す", () => {
    const html = renderToStaticMarkup(
      <RouteError
        error={new Error("Failed to fetch dynamically imported module: /assets/x.js")}
        reset={() => {}}
      />,
    );
    expect(html).toContain("画面の読み込みに失敗しました");
    expect(html).toContain("ページを再読み込み");
  });

  it("RouteError は一般エラーでも再読み込みボタンを出す", () => {
    const html = renderToStaticMarkup(<RouteError error={new Error("boom")} reset={() => {}} />);
    expect(html).toContain("予期せぬエラーが発生しました");
    expect(html).toContain("ページを再読み込み");
  });

  it("RoutePending は日本語の読み込み表示を出す", () => {
    expect(renderToStaticMarkup(<RoutePending />)).toContain("読み込み中");
  });
});
