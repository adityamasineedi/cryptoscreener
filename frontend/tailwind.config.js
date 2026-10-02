/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      screens: {
        term: "900px",
        "term-md": "1100px",
        "term-lg": "1400px",
      },
      colors: {
        terminal: {
          bg: "#0b0f14",
          panel: "#121820",
          border: "#1e2a38",
          muted: "#8b9bb4",
          text: "#e8eef7",
          accent: "#3d9cf0",
          up: "#2ecc71",
          down: "#e74c3c",
          warn: "#f0b429",
        },
      },
      fontFamily: {
        display: ['"IBM Plex Sans"', "Segoe UI", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
