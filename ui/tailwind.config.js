/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0b0d10",
          card: "#15181d",
          hover: "#1c2127",
        },
        border: {
          DEFAULT: "#262b33",
        },
        text: {
          DEFAULT: "#e6e8ec",
          muted: "#8b9099",
        },
        accent: {
          DEFAULT: "#3b82f6",
          hover: "#2563eb",
        },
        success: "#22c55e",
        warning: "#f59e0b",
        danger: "#ef4444",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
