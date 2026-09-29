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
          // The same hue one step darker, for surfaces that carry white text.
          // `accent` cannot do both jobs: white on #3b82f6 is 3.68:1, short of
          // the 4.5:1 AA threshold for 14px text, while #3b82f6 as link text on
          // the page background is 5.29:1 -- and #2563eb would drop that to
          // 3.76:1. Darkening the one token would have fixed every primary
          // button and broken every link in every report.
          solid: "#2563eb",
          "solid-hover": "#1d4ed8",
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
