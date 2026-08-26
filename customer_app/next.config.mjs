/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a self-contained server bundle for a small container image (Cloud Run).
  output: "standalone",
};
export default nextConfig;
