import type { NextConfig } from "next";

const config: NextConfig = {
  // Standalone output is only needed for the Docker image (set NEXT_OUTPUT=standalone).
  // Skipping it elsewhere avoids symlink (EPERM) failures when building on Windows.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  typescript: {
    // Type safety is enforced by `pnpm typecheck` in CI/build scripts.
    ignoreBuildErrors: false,
  },
  images: {
    // Local, self-hosted uploads only; remote images are not used.
    remotePatterns: [],
  },
};

export default config;
