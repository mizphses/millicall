// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildFaxSendFormData,
  canCancel,
  canRetry,
  hasActiveJobs,
  normalizeFaxNumber,
  outboxPdfUrl,
  outboxStatus,
  validateFaxSendForm,
} from "../pages/faxes/outbox";
import { WEBHOOK_EVENT_OPTIONS, webhookEventLabel } from "../pages/webhooks/formPayload";

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ api: { GET: apiGet, POST: apiPost, DELETE: vi.fn() } }));
vi.mock("../hooks/useUrlState", () => ({ useUrlState: () => ["", () => undefined] }));
vi.mock("../toast/ToastProvider", () => ({
  useToast: () => ({ success: toastSuccess, error: toastError }),
}));

const { OutboxPanel } = await import("../pages/faxes/OutboxPanel");

const JOBS = [
  {
    id: 5,
    to_number: "0312345678",
    trunk_name: "Flets02",
    pages: 2,
    status: "queued",
    attempts: 1,
    max_attempts: 3,
    last_error: "話中（USER_BUSY）",
    result_text: "",
    remote_station_id: "",
    has_pdf: true,
    created_by: 1,
    created_at: "2026-11-14T01:00:00",
    started_at: "2026-11-14T01:00:05",
    finished_at: null,
    next_attempt_at: "2026-11-14T01:01:05",
  },
  {
    id: 4,
    to_number: "0611112222",
    trunk_name: "Flets02",
    pages: 1,
    status: "failed",
    attempts: 3,
    max_attempts: 3,
    last_error: "応答なし（NO_ANSWER）",
    result_text: "",
    remote_station_id: "",
    has_pdf: true,
    created_by: 1,
    created_at: "2026-11-13T01:00:00",
    started_at: null,
    finished_at: "2026-11-13T01:10:00",
    next_attempt_at: null,
  },
];

const TRUNKS = [
  { id: 2, name: "Flets02", display_name: "フレッツ", enabled: true },
  { id: 1, name: "Brastel", display_name: "Brastel", enabled: true },
  { id: 3, name: "Off", display_name: "Off", enabled: false },
];

let host: HTMLDivElement, root: Root;

beforeEach(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  apiGet.mockReset();
  apiPost.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/api/trunks") return { data: TRUNKS, error: undefined };
    return { data: { items: JOBS, total: 2 }, error: undefined };
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <OutboxPanel />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function setInput(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}

function setFile(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("OutboxPanel", () => {
  it("送信ボックスに状態・試行回数・エラーと再送/取消ボタンを出す", async () => {
    await render();
    const text = host.textContent ?? "";
    expect(text).toContain("再試行待ち");
    expect(text).toContain("1/3");
    expect(text).toContain("話中（USER_BUSY）");
    expect(text).toContain("失敗");
    expect(text).toContain("全 2 件");
    expect(host.querySelector('[aria-label="送信 FAX 5 を取り消す"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="送信 FAX 5 を再送"]')).toBeNull();
    expect(host.querySelector('[aria-label="送信 FAX 4 を再送"]')).not.toBeNull();
    // トランク選択: 既定（有効なトランクの名前順先頭）＋有効なトランクのみ
    const options = Array.from(host.querySelectorAll("select option")).map((o) => o.textContent);
    expect(options).toEqual(["既定の発信トランク（Brastel）", "Brastel", "フレッツ（Flets02）"]);
  });

  it("入力不備はクライアント側で止め、正しい入力は multipart で送る", async () => {
    await render();
    const form = host.querySelector("form")!;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(host.textContent).toContain("宛先の FAX 番号を入力してください");
    expect(host.textContent).toContain("送信する PDF を選択してください");
    expect(apiPost).not.toHaveBeenCalled();

    apiPost.mockResolvedValue({
      data: { ...JOBS[0], id: 6, status: "queued", attempts: 0, pages: 1 },
      error: undefined,
    });
    const numberInput = host.querySelector<HTMLInputElement>('input[inputmode="tel"]')!;
    const fileInput = host.querySelector<HTMLInputElement>('input[type="file"]')!;
    const select = host.querySelector<HTMLSelectElement>("select")!;
    await act(async () => {
      setInput(numberInput, "03-1234-5678");
      setInput(select, "Flets02");
      setFile(fileInput, new File(["%PDF-1.4"], "doc.pdf", { type: "application/pdf" }));
    });
    expect(host.textContent).toContain("doc.pdf");
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(apiPost).toHaveBeenCalledTimes(1);
    const [path, init] = apiPost.mock.calls[0];
    expect(path).toBe("/api/faxes/outbox");
    const body = init.bodySerializer() as FormData;
    expect(body.get("to_number")).toBe("0312345678");
    expect(body.get("trunk_name")).toBe("Flets02");
    expect((body.get("file") as File).name).toBe("doc.pdf");
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("サーバーのエラー文言をトーストに出す", async () => {
    await render();
    apiPost.mockResolvedValue({ data: undefined, error: { detail: "ページ数が上限を超えています" } });
    const form = host.querySelector("form")!;
    await act(async () => {
      setInput(host.querySelector<HTMLInputElement>('input[inputmode="tel"]')!, "0312345678");
      setFile(
        host.querySelector<HTMLInputElement>('input[type="file"]')!,
        new File(["%PDF-1.4"], "a.pdf", { type: "application/pdf" }),
      );
    });
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(toastError).toHaveBeenCalledWith("ページ数が上限を超えています");
  });
});

describe("faxes/outbox", () => {
  const pdf = new File(["%PDF"], "a.pdf", { type: "application/pdf" });

  it("宛先番号の正規化と検証", () => {
    expect(normalizeFaxNumber("03-1234-5678")).toBe("0312345678");
    expect(validateFaxSendForm({ toNumber: "0312345678", trunkName: "", file: pdf })).toEqual({});
    expect(validateFaxSendForm({ toNumber: "010441234567", trunkName: "", file: pdf }).toNumber).toMatch(
      "国内番号",
    );
    expect(validateFaxSendForm({ toNumber: "1234", trunkName: "", file: pdf }).toNumber).toBeDefined();
    const png = new File(["x"], "a.png", { type: "image/png" });
    expect(validateFaxSendForm({ toNumber: "0312345678", trunkName: "", file: png }).file).toBe(
      "PDF ファイルを選択してください",
    );
  });

  it("multipart 本文（トランク未指定なら送らない）", () => {
    const body = buildFaxSendFormData({ toNumber: "06 1111 2222", trunkName: "", file: pdf });
    expect(body.get("to_number")).toBe("0611112222");
    expect(body.has("trunk_name")).toBe(false);
  });

  it("状態表示・操作可否・自動更新・URL", () => {
    expect(outboxStatus({ status: "queued", attempts: 0, next_attempt_at: null })).toEqual({
      label: "送信待ち",
      tone: "neutral",
    });
    expect(outboxStatus({ status: "sending", attempts: 1, next_attempt_at: null }).label).toBe("送信中");
    expect(outboxStatus({ status: "sent", attempts: 1, next_attempt_at: null }).tone).toBe("success");
    expect(canRetry({ status: "failed" })).toBe(true);
    expect(canRetry({ status: "sent" })).toBe(false);
    expect(canCancel({ status: "queued" })).toBe(true);
    expect(canCancel({ status: "sending" })).toBe(false);
    expect(hasActiveJobs([{ status: "sent" }, { status: "sending" }])).toBe(true);
    expect(hasActiveJobs([{ status: "failed" }])).toBe(false);
    expect(outboxPdfUrl(9)).toBe("/api/faxes/outbox/9/pdf");
  });

  it("Webhook のイベント選択に FAX 送信の 2 イベントがある", () => {
    const values = WEBHOOK_EVENT_OPTIONS.map((o) => o.value);
    expect(values).toContain("fax.sent");
    expect(values).toContain("fax.failed");
    expect(webhookEventLabel("fax.sent")).toBe("FAX 送信完了");
    expect(webhookEventLabel("fax.failed")).toBe("FAX 送信失敗");
  });
});
