/** Reconcile server refreshes without replacing a user's unsaved edits. */
export function mergeServerForm<T extends Record<string, string | boolean>>(
  current: T, previous: T | undefined, next: T,
): T {
  if (!previous) return next;
  return Object.fromEntries(Object.keys(next).map((key) => [
    key, current[key] !== previous[key] ? current[key] : next[key],
  ])) as T;
}

/** Clear only the submitted edits; preserve input made while a save was pending. */
export function acceptSavedForm<T extends Record<string, string | boolean>>(
  current: T, previous: T | undefined, next: T, submitted: T, keys: string[],
): T {
  const merged = mergeServerForm(current, previous, next);
  for (const key of keys) {
    if (current[key] === submitted[key]) merged[key as keyof T] = next[key as keyof T];
  }
  return merged;
}

export class FormValidationError extends Error {
  constructor(public fieldErrors: Record<string, string>, message = "入力内容を確認してください") {
    super(message);
  }
}

/** openapi-fetch has already consumed the response body; use its parsed error. */
export function apiError(error: unknown, fallback: string): Error {
  const detail = error && typeof error === "object" && "detail" in error ? error.detail : undefined;
  if (typeof detail === "string") {
    const match = /^([a-z][a-z0-9_]+)(?::| の)/.exec(detail);
    return match ? new FormValidationError({ [match[1]]: detail }, detail) : new Error(detail);
  }
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {};
    for (const item of detail) {
      if (item && Array.isArray(item.loc) && typeof item.msg === "string") {
        fields[String(item.loc.at(-1))] = item.msg;
      }
    }
    return new FormValidationError(fields, Object.values(fields).join(" / ") || fallback);
  }
  return new Error(fallback);
}
