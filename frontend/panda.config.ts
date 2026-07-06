import { defineConfig, defineRecipe } from "@pandacss/dev";

// フラットデザインのデザイントークン。
// 生値（色/px）はここに集約し、コンポーネントはトークン経由でのみ参照する。

const buttonRecipe = defineRecipe({
  className: "button",
  description: "汎用ボタン",
  base: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "2",
    fontSize: "sm",
    fontWeight: "500",
    lineHeight: "1",
    borderRadius: "md",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    transition: "background-color 0.12s, border-color 0.12s, color 0.12s",
    userSelect: "none",
    whiteSpace: "nowrap",
    _disabled: { opacity: 0.5, cursor: "not-allowed" },
  },
  variants: {
    variant: {
      primary: {
        bg: "accent",
        borderColor: "accent",
        color: "white",
        _hover: { bg: "accent.hover", borderColor: "accent.hover" },
      },
      secondary: {
        bg: "white",
        borderColor: "border",
        color: "text",
        _hover: { bg: "gray.50" },
      },
      danger: {
        bg: "white",
        borderColor: "danger",
        color: "danger",
        _hover: { bg: "danger.soft" },
      },
      ghost: {
        bg: "transparent",
        borderColor: "transparent",
        color: "text.muted",
        _hover: { bg: "gray.50", color: "text" },
      },
    },
    size: {
      sm: { h: "7", px: "3", fontSize: "sm" },
      md: { h: "9", px: "4", fontSize: "md" },
    },
  },
  defaultVariants: { variant: "primary", size: "md" },
});

const inputRecipe = defineRecipe({
  className: "input",
  description: "テキスト入力・セレクト・テキストエリア共通",
  base: {
    display: "block",
    width: "100%",
    fontSize: "md",
    color: "text",
    bg: "white",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "border",
    borderRadius: "md",
    px: "3",
    py: "2",
    transition: "border-color 0.12s",
    _placeholder: { color: "text.subtle" },
    _hover: { borderColor: "border.strong" },
    _focus: { outline: "none", borderColor: "accent" },
    _disabled: { bg: "gray.50", opacity: 0.7, cursor: "not-allowed" },
  },
  variants: {
    invalid: {
      true: { borderColor: "danger", _focus: { borderColor: "danger" } },
    },
  },
});

const tableRecipe = defineRecipe({
  className: "table",
  description: "データテーブル",
  base: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "md",
    color: "text",
    "& thead th": {
      textAlign: "left",
      fontWeight: "600",
      fontSize: "sm",
      color: "text.muted",
      bg: "gray.50",
      px: "4",
      py: "3",
      borderBottomWidth: "1px",
      borderBottomStyle: "solid",
      borderBottomColor: "border",
      whiteSpace: "nowrap",
    },
    "& tbody td": {
      px: "4",
      py: "3",
      borderBottomWidth: "1px",
      borderBottomStyle: "solid",
      borderBottomColor: "border",
      verticalAlign: "middle",
    },
    "& tbody tr:hover": { bg: "gray.50" },
  },
});

const panelRecipe = defineRecipe({
  className: "panel",
  description: "白カード / パネル（影なし・1px ボーダー）",
  base: {
    bg: "white",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "border",
    borderRadius: "lg",
  },
});

const badgeRecipe = defineRecipe({
  className: "badge",
  description: "状態バッジ",
  base: {
    display: "inline-flex",
    alignItems: "center",
    gap: "1",
    fontSize: "sm",
    fontWeight: "500",
    lineHeight: "1",
    px: "2",
    py: "1",
    borderRadius: "sm",
    borderWidth: "1px",
    borderStyle: "solid",
    whiteSpace: "nowrap",
  },
  variants: {
    tone: {
      neutral: { bg: "gray.50", color: "text.muted", borderColor: "border" },
      success: { bg: "success.soft", color: "success.text", borderColor: "success" },
      warn: { bg: "warn.soft", color: "warn.text", borderColor: "warn" },
      danger: { bg: "danger.soft", color: "danger.text", borderColor: "danger" },
      accent: { bg: "accent.soft", color: "accent.text", borderColor: "accent" },
    },
  },
  defaultVariants: { tone: "neutral" },
});

export default defineConfig({
  preflight: true,
  presets: ["@pandacss/preset-base"],
  include: ["./src/**/*.{ts,tsx}"],
  exclude: [],
  jsxFramework: "react",
  outdir: "styled-system",
  theme: {
    extend: {
      tokens: {
        colors: {
          white: { value: "#ffffff" },
          gray: {
            50: { value: "#f7f8f8" },
            100: { value: "#eceef0" },
            200: { value: "#dfe2e6" },
            300: { value: "#c9ced4" },
            400: { value: "#9aa2ac" },
            500: { value: "#6b747f" },
            600: { value: "#4b535c" },
            700: { value: "#343b42" },
            800: { value: "#22272c" },
            900: { value: "#14181c" },
          },
          teal: {
            50: { value: "#eef7f6" },
            100: { value: "#d3ebe8" },
            500: { value: "#127c73" },
            600: { value: "#0f6a62" },
            700: { value: "#0c574f" },
          },
          green: { 50: { value: "#eaf6ee" }, 500: { value: "#2e844a" }, 700: { value: "#1f6236" } },
          amber: { 50: { value: "#fbf3e2" }, 500: { value: "#a86a11" }, 700: { value: "#7d4e08" } },
          red: { 50: { value: "#fbecec" }, 500: { value: "#c0392b" }, 700: { value: "#8f261b" } },
        },
        spacing: {
          "1": { value: "4px" },
          "2": { value: "8px" },
          "3": { value: "12px" },
          "4": { value: "16px" },
          "5": { value: "20px" },
          "6": { value: "24px" },
          "7": { value: "28px" },
          "8": { value: "32px" },
          "10": { value: "40px" },
          "12": { value: "48px" },
        },
        fontSizes: {
          sm: { value: "12px" },
          md: { value: "14px" },
          lg: { value: "18px" },
          xl: { value: "24px" },
        },
        radii: {
          sm: { value: "4px" },
          md: { value: "6px" },
          lg: { value: "10px" },
        },
        sizes: {
          sidebar: { value: "232px" },
          header: { value: "56px" },
          panel: { value: "440px" },
          toast: { value: "320px" },
          dialog: { value: "400px" },
          loginCard: { value: "360px" },
          providerCard: { value: "280px" },
          kindCard: { value: "150px" },
          textareaMin: { value: "120px" },
        },
      },
      semanticTokens: {
        colors: {
          bg: { value: "{colors.white}" },
          "bg.muted": { value: "{colors.gray.50}" },
          text: { value: "{colors.gray.900}" },
          "text.muted": { value: "{colors.gray.600}" },
          "text.subtle": { value: "{colors.gray.400}" },
          border: { value: "{colors.gray.200}" },
          "border.strong": { value: "{colors.gray.300}" },
          accent: { value: "{colors.teal.500}" },
          "accent.hover": { value: "{colors.teal.600}" },
          "accent.text": { value: "{colors.teal.700}" },
          "accent.soft": { value: "{colors.teal.50}" },
          success: { value: "{colors.green.500}" },
          "success.text": { value: "{colors.green.700}" },
          "success.soft": { value: "{colors.green.50}" },
          warn: { value: "{colors.amber.500}" },
          "warn.text": { value: "{colors.amber.700}" },
          "warn.soft": { value: "{colors.amber.50}" },
          danger: { value: "{colors.red.500}" },
          "danger.text": { value: "{colors.red.700}" },
          "danger.soft": { value: "{colors.red.50}" },
        },
      },
      recipes: {
        button: buttonRecipe,
        input: inputRecipe,
        table: tableRecipe,
        panel: panelRecipe,
        badge: badgeRecipe,
      },
    },
  },
  globalCss: {
    "html, body, #root": { height: "100%" },
    body: {
      margin: 0,
      bg: "bg.muted",
      color: "text",
      fontFamily:
        'system-ui, -apple-system, "Segoe UI", Roboto, "Hiragino Kaku Gothic ProN", "Noto Sans JP", Meiryo, sans-serif',
      fontSize: "md",
      lineHeight: "1.5",
      WebkitFontSmoothing: "antialiased",
    },
    "*": { boxSizing: "border-box" },
  },
});
