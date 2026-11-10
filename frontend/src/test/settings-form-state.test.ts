import { describe, expect, it } from "vitest";
import { acceptSavedForm, apiError, FormValidationError, mergeServerForm } from "../pages/settings/formState";

describe("settings and network form reconciliation", () => {
  const previous = { smtp_port: "587", vad_mode: "1", smtp_password: "" };
  it("keeps another section's unsaved input after a server update", () => {
    const current = { ...previous, smtp_port: "2525" };
    expect(mergeServerForm(current, previous, { ...previous, vad_mode: "2" })).toEqual({ ...current, vad_mode: "2" });
  });
  it("accepts saved fields but retains unrelated dirty fields", () => {
    const current = { ...previous, smtp_port: "2525", vad_mode: "2" };
    expect(acceptSavedForm(current, previous, { ...previous, vad_mode: "2" }, current, ["vad_mode"])).toEqual(current);
  });
  it("retains an edit entered while its earlier value was saving", () => {
    const submitted = { ...previous, smtp_port: "2525" };
    const current = { ...submitted, smtp_port: "1025" };
    expect(acceptSavedForm(current, previous, submitted, submitted, ["smtp_port"]).smtp_port).toBe("1025");
  });
  it("clears the submitted secret and retains a new secret typed during the request", () => {
    const submitted = { ...previous, smtp_password: "first-secret" };
    expect(acceptSavedForm(submitted, previous, previous, submitted, ["smtp_password"]).smtp_password).toBe("");
    expect(acceptSavedForm({ ...submitted, smtp_password: "new-secret" }, previous, previous, submitted, ["smtp_password"]).smtp_password).toBe("new-secret");
  });
  it("loads the first server response without treating empty defaults as dirty", () => {
    expect(mergeServerForm({} as typeof previous, undefined, previous)).toEqual(previous);
  });
});

describe("parsed API validation errors", () => {
  it("keeps the server detail and field association without reading response.json again", () => {
    const error = apiError({ detail: "smtp_port: 1〜65535 で指定してください" }, "fallback");
    expect(error).toBeInstanceOf(FormValidationError);
    expect((error as FormValidationError).fieldErrors.smtp_port).toContain("65535");
  });
  it("maps FastAPI validation locations to the corresponding input", () => {
    const error = apiError({ detail: [{ loc: ["body", "lan_prefix"], msg: "32 以下を指定してください" }] }, "fallback");
    expect((error as FormValidationError).fieldErrors).toEqual({ lan_prefix: "32 以下を指定してください" });
  });
});
