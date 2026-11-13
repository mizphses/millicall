// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigPanel } from "../pages/workflows/ConfigPanel";
import type { ConfigDrafts } from "../pages/workflows/editorState";
import { AGENTS_KEY, PROVIDERS_KEY, WORKFLOWS_KEY } from "../queryKeys";

const changed = vi.fn();
let host: HTMLDivElement, root: Root, client: QueryClient;
function Harness() {
  const [nodeId, setNodeId] = useState("first");
  const [drafts, setDrafts] = useState<ConfigDrafts>({});
  const [config, setConfig] = useState<Record<string, unknown>>({
    intents: { sales: "営業", support: "支援" },
    payload: { answer: 42 },
  });
  return (
    <>
      <button
        onClick={() => setNodeId(nodeId === "first" ? "second" : "first")}
      >
        別のノードに切替
      </button>
      <ConfigPanel
        nodeId={nodeId}
        nodeLabel="意図判定"
        config={config}
        schema={[
          {
            key: "intents",
            type: "key_value_list",
            label: "意図",
            required: true,
          },
          { key: "payload", type: "json", label: "送信内容", required: false },
        ]}
        currentWorkflowId={1}
        onChange={(key, value) => {
          changed(key, value);
          setConfig((previous) => ({ ...previous, [key]: value }));
        }}
        onDelete={() => undefined}
        drafts={drafts}
        onDraftChange={(key, draft) =>
          setDrafts((previous) => ({ ...previous, [key]: draft }))
        }
      />
    </>
  );
}
async function edit(
  element: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      element instanceof HTMLInputElement
        ? HTMLInputElement.prototype
        : HTMLTextAreaElement.prototype,
      "value",
    )!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
beforeEach(async () => {
  changed.mockClear();
  (
    globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  for (const key of [AGENTS_KEY, PROVIDERS_KEY, WORKFLOWS_KEY])
    client.setQueryData(key, []);
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    ),
  );
});
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  host.remove();
});

describe("config drafts preserve incomplete input", () => {
  it("preserves both duplicate-key rows while switching nodes, then commits after correction", async () => {
    await edit(
      host.querySelectorAll<HTMLInputElement>("fieldset input")[2],
      "sales",
    );
    expect(host.querySelectorAll("fieldset input")).toHaveLength(4);
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("重複");
    expect(changed).not.toHaveBeenCalled();
    await act(async () =>
      host.querySelector<HTMLButtonElement>("button")!.click(),
    );
    await act(async () =>
      host.querySelector<HTMLButtonElement>("button")!.click(),
    );
    expect(
      host.querySelectorAll<HTMLInputElement>("fieldset input")[2].value,
    ).toBe("sales");
    expect(
      host.querySelectorAll<HTMLInputElement>("fieldset input")[3].value,
    ).toBe("支援");
    await edit(
      host.querySelectorAll<HTMLInputElement>("fieldset input")[2],
      "help",
    );
    expect(changed).toHaveBeenLastCalledWith("intents", {
      sales: "営業",
      help: "支援",
    });
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });
  it("retains invalid JSON text without replacing the saved config with a string", async () => {
    const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
    await edit(textarea, '{"answer":');
    expect(textarea.value).toBe('{"answer":');
    expect(textarea.getAttribute("aria-invalid")).toBe("true");
    expect(changed).not.toHaveBeenCalled();
    await edit(textarea, '{"answer":43}');
    expect(changed).toHaveBeenLastCalledWith("payload", { answer: 43 });
    expect(textarea.getAttribute("aria-invalid")).toBe("false");
  });
});
