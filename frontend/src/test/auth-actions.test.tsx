// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "../pages/LoginPage";
import { Header } from "../shell/Header";
import { SecurityPage } from "../pages/SecurityPage";
import { AUTH_ME_KEY } from "../queryKeys";

const mocks = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn(), navigate: vi.fn(), success: vi.fn(), error: vi.fn(), warn: vi.fn(), dirtyState: vi.fn() }));
vi.mock("../api/client", () => ({ api: { POST: mocks.post, GET: mocks.get }, ensureCsrfCookie: vi.fn() }));
vi.mock("../auth/auth", () => ({ fetchLoginConfig: async () => ({ saml_enabled: false }), postLoginPath: () => "/" }));
vi.mock("@tanstack/react-router", () => ({ useRouter: () => ({ navigate: mocks.navigate }) }));
vi.mock("../hooks/useDirtyState", () => ({ getDirtyState: mocks.dirtyState }));
vi.mock("../components/UnsavedChangesGuard", () => ({ UnsavedChangesGuard: () => null }));
vi.mock("../toast/ToastProvider", () => ({ useToast: () => ({ success: mocks.success, error: mocks.error, warn: mocks.warn }) }));
let host: HTMLDivElement, root: Root, client: QueryClient;
async function render(element: React.ReactNode) {
  await act(async () => { root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>); });
}
async function clickText(text: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.includes(text));
  if (!button) throw new Error(`Missing button: ${text}`);
  await act(async () => { button.click(); });
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)); }); }
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  mocks.dirtyState.mockReturnValue({ dirty: false, busy: false });
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); });

describe("authentication error paths", () => {
  it("keeps the post-2FA explanation during StrictMode's repeated initialization", async () => {
    sessionStorage.setItem("millicall.authNotice", "2FA を無効にしました。もう一度ログインしてください。");
    await render(<React.StrictMode><LoginPage /></React.StrictMode>);
    expect(host.querySelector('[role="status"]')?.textContent).toContain("もう一度ログインしてください");
    expect(sessionStorage.getItem("millicall.authNotice")).toBeNull();
  });
  it("shows a retryable error when login transport fails", async () => {
    mocks.post.mockRejectedValue(new TypeError("Failed to fetch"));
    await render(<LoginPage />);
    await act(async () => { host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("サーバーに接続できません");
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
  it("does not clear the session or report success for logout HTTP 500", async () => {
    mocks.post.mockResolvedValue({ response: new Response(null, { status: 500 }), error: { detail: "failed" } });
    client.setQueryData(AUTH_ME_KEY, { username: "admin" });
    await render(<Header username="admin" />); await clickText("ログアウト");
    expect(mocks.error).toHaveBeenCalled(); expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(client.getQueryData(AUTH_ME_KEY)).toEqual({ username: "admin" });
  });
  it("asks before sending logout when a page has unsaved changes", async () => {
    mocks.dirtyState.mockReturnValue({ dirty: true, busy: false });
    await render(<Header username="admin" />); await clickText("ログアウト");
    expect(host.querySelector('[role="alertdialog"]')?.textContent).toContain("未保存の変更は失われます");
    expect(mocks.post).not.toHaveBeenCalled();
    await clickText("キャンセル");
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("prevents logout while a save is pending", async () => {
    mocks.dirtyState.mockReturnValue({ dirty: true, busy: true });
    await render(<Header username="admin" />); await clickText("ログアウト");
    expect(mocks.warn).toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("shows unknown 2FA state and a retry action when loading fails", async () => {
    mocks.get.mockResolvedValue({ error: { detail: "unavailable" } });
    await render(<SecurityPage />); await settle();
    expect(host.textContent).toContain("2FA の状態を確認できません");
    expect(host.textContent).not.toContain("無効です。");
    expect(host.textContent).not.toContain("2FA を設定する");
    expect(host.textContent).toContain("再試行");
  });
  it("clears stale auth data and redirects with an explanation after disabling 2FA", async () => {
    mocks.get.mockResolvedValue({ data: { id: 1, username: "admin", totp_enabled: true } });
    mocks.post.mockResolvedValue({ response: new Response(null, { status: 200 }) });
    await render(<SecurityPage />); await settle();
    const code = host.querySelector<HTMLInputElement>("#security-reauth")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(code, "123456");
      code.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await clickText("2FA を無効にする");
    const confirm = [...host.querySelectorAll("button")].find((item) => item.textContent === "無効にする")!;
    await act(async () => confirm.click());
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/login" });
    expect(sessionStorage.getItem("millicall.authNotice")).toContain("もう一度ログインしてください");
    expect(client.getQueryData(AUTH_ME_KEY)).toBeUndefined();
  });
});
