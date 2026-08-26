import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        navy:  "#1E293B",
        brand: "#2563EB",
        sky:   "#0EA5E9",
        teal:  "#0D9488",
        amber: "#D97706",
        ok:    "#059669",
        danger:"#DC2626",
      },
    },
  },
  plugins: [],
};
export default config;
