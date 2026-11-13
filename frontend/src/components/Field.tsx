import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";
import { css } from "styled-system/css";

export function Field({
  label,
  children,
  error,
  help,
  required,
}: {
  label: string;
  children: ReactNode;
  error?: string | null;
  help?: ReactNode;
  required?: boolean;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const controlId = `field-${id}`;
  const isRequired = required ?? /\*\s*$/.test(label);
  const cleanLabel = label
    .replace(/\s*\*\s*$/, "")
    .replace(/（(?:任意|省略可)[・、]?([^）]*)）/g, (_, rest: string) =>
      rest ? `（${rest}）` : "",
    );
  let attached = false;
  const attach = (nodes: ReactNode): ReactNode =>
    Children.map(nodes, (child) => {
      if (!isValidElement(child)) return child;
      const element = child as ReactElement<
        Record<string, unknown> & { children?: ReactNode }
      >;
      if (
        !attached &&
        ["input", "select", "textarea"].includes(String(element.type))
      ) {
        attached = true;
        return cloneElement(element, {
          id: controlId,
          "aria-invalid": error ? true : undefined,
          "aria-describedby":
            [error && `${controlId}-error`, help && `${controlId}-help`]
              .filter(Boolean)
              .join(" ") || undefined,
          "aria-required": isRequired || undefined,
        });
      }
      return element.props.children
        ? cloneElement(element, {}, attach(element.props.children))
        : child;
    });
  const content = attach(children);
  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => {
      const scope = ref.current?.closest("dialog, form, main") ?? document;
      const first = scope.querySelector<HTMLElement>('[aria-invalid="true"]');
      if (first && ref.current?.contains(first)) first.focus();
    }, 0);
    return () => clearTimeout(timer);
  }, [error]);
  return (
    <div ref={ref}>
      <label
        htmlFor={controlId}
        className={css({
          display: "block",
          fontSize: "sm",
          fontWeight: "500",
          color: "text.muted",
          mb: "1",
        })}
      >
        {cleanLabel}
        <span className={css({ ml: "2", fontWeight: "400" })}>
          {isRequired ? "必須" : "任意"}
        </span>
      </label>
      {content}
      {help && (
        <div
          id={`${controlId}-help`}
          className={css({ fontSize: "sm", color: "text.muted", mt: "1" })}
        >
          {help}
        </div>
      )}
      {error && (
        <p
          id={`${controlId}-error`}
          role="alert"
          className={css({ fontSize: "sm", color: "danger.text", mt: "1" })}
        >
          {error}
        </p>
      )}
    </div>
  );
}
