// @vitest-environment jsdom
/**
 * 電話機の内線割当パネル（issue #149）のテスト。
 *
 * - extensionAssign.ts の純粋ロジック（セレクト項目・バリデーション・ペイロード）
 * - DevicesPage のパネル: 既存の内線を選ぶ / 新規作成の切替、別端末に割当済みの内線を
 *   選んだときの付け替え確認、割当解除の確認
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildAssignPayload,
  emptyAssignForm,
  extensionOptions,
  validateAssignForm,
} from "../pages/devices/extensionAssign";
import type { components } from "../api/schema.d";

type DeviceRead = components["schemas"]["DeviceRead"];
type ExtensionRead = components["schemas"]["ExtensionRead"];

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const apiPatch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({
  api: { GET: apiGet, POST: apiPost, PATCH: apiPatch, DELETE: vi.fn() },
}));
vi.mock("../hooks/useUrlState", () => ({ useUrlState: () => ["", () => undefined] }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="#">{children}</a>,
}));
vi.mock("../components/UnsavedChangesGuard", () => ({ UnsavedChangesGuard: () => null }));
vi.mock("../toast/ToastProvider", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), show: vi.fn() }),
}));

const { DevicesPage } = await import("../pages/DevicesPage");

function device(overrides: Partial<DeviceRead>): DeviceRead {
  return {
    id: 1,
    mac_address: "AA:00:00:00:00:01",
    ip_address: "10.0.0.11",
    hostname: null,
    model: "T54W",
    extension_id: null,
    extension_number: null,
    extension_display_name: null,
    provisioned: false,
    last_seen: null,
    active: true,
    created_at: "2026-11-14T00:00:00",
    provision_mode: "standard",
    token_issued: false,
    last_config_fetch_at: null,
    last_config_fetch_user_agent: null,
    last_config_fetch_used_token: false,
    ...overrides,
  };
}

function extension(overrides: Partial<ExtensionRead>): ExtensionRead {
  return {
    id: 10,
    number: "1001",
    display_name: "受付",
    enabled: true,
    calling_permission: "domestic",
    devices: [],
    ...overrides,
  };
}

const NEW_PHONE = device({ id: 1, mac_address: "AA:00:00:00:00:01" });
const HOLDER = device({
  id: 2,
  mac_address: "AA:00:00:00:00:02",
  model: "KX-HDV230",
  ip_address: "10.0.0.12",
  extension_id: 11,
  extension_number: "1002",
  extension_display_name: "営業",
  provisioned: true,
});
const EXTENSIONS = [
  extension({ id: 10, number: "1001", display_name: "受付" }),
  extension({ id: 11, number: "1002", display_name: "営業" }),
];

// ---------------------------------------------------------------------------
// 純粋ロジック
// ---------------------------------------------------------------------------

describe("extensionOptions", () => {
  it("別の端末に割当済みの内線には端末を添えて表示する", () => {
    const options = extensionOptions(EXTENSIONS, [NEW_PHONE, HOLDER], NEW_PHONE.id);
    expect(options.map((o) => o.label)).toEqual([
      "1001 受付",
      "1002 営業（端末 AA:00:00:00:00:02（KX-HDV230） に割当済み）",
    ]);
    expect(options[1].holder?.id).toBe(HOLDER.id);
  });

  it("割当先の端末自身に紐づく内線は割当済み扱いにしない", () => {
    const options = extensionOptions(EXTENSIONS, [NEW_PHONE, HOLDER], HOLDER.id);
    expect(options.every((o) => o.holder === null)).toBe(true);
  });
});

describe("validateAssignForm / buildAssignPayload", () => {
  it("既存選択では内線の未選択をエラーにする", () => {
    expect(validateAssignForm(emptyAssignForm()).extension_id).toBeDefined();
  });

  it("新規作成では番号と表示名を必須にする", () => {
    const errors = validateAssignForm(emptyAssignForm("new"));
    expect(errors.extension_number).toBeDefined();
    expect(errors.display_name).toBeDefined();
  });

  it("既存選択は extension_id だけを送る", () => {
    const body = buildAssignPayload({ ...emptyAssignForm(), extension_id: "11" }, true);
    expect(body).toEqual({ extension_id: 11, reassign: true });
  });

  it("新規作成はトリムした番号と表示名だけを送る", () => {
    const body = buildAssignPayload(
      { ...emptyAssignForm("new"), extension_number: " 1003 ", display_name: " 総務 " },
      false,
    );
    expect(body).toEqual({ extension_number: "1003", display_name: "総務", reassign: false });
  });
});

// ---------------------------------------------------------------------------
// DevicesPage のパネル
// ---------------------------------------------------------------------------

let host: HTMLDivElement, root: Root;

beforeEach(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom は <dialog> の showModal/close を実装していないため最小限の代替を入れる
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/api/devices") return { data: [NEW_PHONE, HOLDER], error: undefined };
    if (path === "/api/extensions") return { data: EXTENSIONS, error: undefined };
    if (path === "/api/devices/registrations")
      return { data: { available: false, registered_device_ids: [] }, error: undefined };
    return { data: undefined, error: { detail: "not mocked" } };
  });
  apiPost.mockResolvedValue({
    data: { ...NEW_PHONE, extension_id: 10, provisioned: true },
    error: undefined,
    response: { status: 200 },
  });
  apiPatch.mockResolvedValue({
    data: { ...HOLDER, extension_id: null, provisioned: false },
    error: undefined,
    response: { status: 200 },
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <DevicesPage />
      </QueryClientProvider>,
    );
  });
  await flush();
}

function buttonByText(text: string): HTMLButtonElement {
  const found = Array.from(document.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === text,
  );
  if (!found) throw new Error(`button not found: ${text}`);
  return found;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await flush();
}

async function selectValue(select: HTMLSelectElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
    setter.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function submitPanel() {
  const form = document.getElementById("quick-provision-form") as HTMLFormElement;
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await flush();
}

describe("DevicesPage 内線割当パネル", () => {
  it("割当済み端末には「内線を変更」「割当を解除」、未割当には「内線割当」を出す", async () => {
    await renderPage();
    expect(document.body.textContent).toContain("内線割当");
    expect(document.body.textContent).toContain("内線を変更");
    expect(document.body.textContent).toContain("割当を解除");
  });

  it("既定は既存の内線セレクトで、新規作成に切り替えると番号・表示名の入力になる", async () => {
    await renderPage();
    await click(buttonByText("内線割当"));

    const select = document.querySelector<HTMLSelectElement>("#quick-provision-form select");
    expect(select).not.toBeNull();
    const labels = Array.from(select!.options).map((o) => o.textContent);
    expect(labels).toContain("1001 受付");
    expect(labels).toContain("1002 営業（端末 AA:00:00:00:00:02（KX-HDV230） に割当済み）");

    const newRadio = document.querySelector<HTMLInputElement>('input[value="new"]')!;
    await click(newRadio);
    expect(document.querySelector("#quick-provision-form select")).toBeNull();
    expect(document.querySelector('#quick-provision-form input[placeholder="1001"]')).not.toBeNull();
  });

  it("未割当の内線を選ぶと確認なしで extension_id を送る", async () => {
    await renderPage();
    await click(buttonByText("内線割当"));
    await selectValue(
      document.querySelector<HTMLSelectElement>("#quick-provision-form select")!,
      "10",
    );
    await submitPanel();

    expect(apiPost).toHaveBeenCalledTimes(1);
    const [path, opts] = apiPost.mock.calls[0];
    expect(path).toBe("/api/devices/{device_id}/quick-provision");
    expect(opts.params.path.device_id).toBe(NEW_PHONE.id);
    expect(opts.body).toEqual({ extension_id: 10, reassign: false });
  });

  it("別の端末に割当済みの内線を選ぶと付け替えを確認し、reassign: true で送る", async () => {
    await renderPage();
    await click(buttonByText("内線割当"));
    await selectValue(
      document.querySelector<HTMLSelectElement>("#quick-provision-form select")!,
      "11",
    );
    await submitPanel();

    // まだ送らず確認ダイアログを出す
    expect(apiPost).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("内線を付け替える");
    expect(document.body.textContent).toContain("AA:00:00:00:00:02");

    await click(buttonByText("付け替える"));
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(apiPost.mock.calls[0][1].body).toEqual({ extension_id: 11, reassign: true });
  });

  it("割当を解除は確認のうえ PATCH で extension_id: null を送る", async () => {
    await renderPage();
    await click(buttonByText("割当を解除"));
    expect(document.body.textContent).toContain("内線の割当を解除");

    await click(buttonByText("解除"));
    expect(apiPatch).toHaveBeenCalledTimes(1);
    const [path, opts] = apiPatch.mock.calls[0];
    expect(path).toBe("/api/devices/{device_id}");
    expect(opts.params.path.device_id).toBe(HOLDER.id);
    expect(opts.body).toEqual({ extension_id: null, reassign: false });
  });
});
