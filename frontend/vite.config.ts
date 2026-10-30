import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks only to this dev server; /api is forwarded to the local backend.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8010" },
  },
});
