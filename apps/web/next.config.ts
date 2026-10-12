import type { NextConfig } from "next";

const documentHeaders = [
  {
    key: "Cache-Control",
    value: "no-cache",
  },
];

const documentRoutes = [
  "/",
  "/login",
  "/admin",
  "/docs/:path*",
  "/oauth/consent",
  "/scenario-runs/:path*",
  "/scenarios/:path*",
];

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  transpilePackages: ["@rhetorica/ui"],
  async headers() {
    return documentRoutes.map((source) => ({
      source,
      headers: documentHeaders,
    }));
  },
  devIndicators: {
    position: "bottom-left",
  },
};

export default nextConfig;
