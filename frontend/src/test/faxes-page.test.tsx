// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { faxPdfUrl, faxResult, faxSender, totalPages } from "../pages/faxes/format";

const apiGet = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ api: { GET: apiGet, DELETE: vi.fn() } }));
vi.mock("../hooks/useUrlState", () => ({ useUrlState: () => ["", () => undefined] }));
vi.mock("../components/ObjectLink", () => ({
  ObjectLink: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
vi.mock("../toast/ToastProvider", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

const { FaxesPage } = await import("../pages/FaxesPage");

const FAXES = [
  {
    id: 12,
    call_uuid: "u-12",
    from_number: "0312345678",
    to_number: "0445895784",
    remote_station_id: "",
    pages: 3,
    success: true,
    result_text: "OK",
    has_pdf: true,
    received_at: "2026-11-14T01:02:03",
  },
  {
    id: 11,
    call_uuid: "u-11",
    from_number: "",
    to_number: "0445895784",
    remote_station_id: "+81 3 9999",
    pages: 0,
    success: false,
    result_text: "Disconnected after permitted retries",
    has_pdf: false,
    received_at: "2026-11-13T01:02:03",
  },
];

let host: HTMLDivElement, root: Root;

beforeEach(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  apiGet.mockReset();
  apiGet.mockResolvedValue({ data: { items: FAXES, total: 2 }, error: undefined });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("FaxesPage", () => {
  it("受信 FAX をテーブル表示し、PDF リンクと結果を出す", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <FaxesPage />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    const text = host.textContent ?? "";
    for (const header of ["受信日時", "発信元", "宛先", "ページ数", "結果", "ダウンロード", "削除"]) {
      expect(text).toContain(header);
    }
    expect(text).toContain("0312345678");
    expect(text).toContain("+81 3 9999");
    expect(text).toContain("成功");
    expect(text).toContain("失敗（Disconnected after permitted retries）");
    expect(text).toContain("全 2 件");
    const links = Array.from(host.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(links).toEqual(["/api/faxes/12/pdf"]);
    expect(apiGet).toHaveBeenCalledWith("/api/faxes", {
      params: { query: { limit: 50, offset: 0 } },
    });
  });
});

describe("faxes/format", () => {
  it("URL・結果・発信元・ページ数", () => {
    expect(faxPdfUrl(5)).toBe("/api/faxes/5/pdf");
    expect(faxResult({ success: true, result_text: "OK" })).toEqual({ label: "成功", tone: "success" });
    expect(faxResult({ success: false, result_text: "" }).label).toBe("失敗");
    expect(faxSender({ from_number: "", remote_station_id: "" })).toBe("非通知");
    expect(totalPages(0, 50)).toBe(1);
    expect(totalPages(101, 50)).toBe(3);
  });
});
