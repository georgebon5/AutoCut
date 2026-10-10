/** @type {import('next').NextConfig} */
const API_URL = process.env.AUTOCUT_API_URL ?? "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  // Proxy /api/* calls in the browser straight to the FastAPI backend so
  // we don't need CORS during dev. In prod the frontend is served behind
  // the same origin via a reverse proxy, so this rewrite becomes a no-op.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/:path*` },
    ];
  },
};

export default nextConfig;
