/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    esmExternals: 'loose',
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  env: {
    API_BASE_URL: process.env.API_BASE_URL,
    GITHUB_CLIENT_ID: process.env.GITHUB_CLIENT_ID,
    GOOGLE_CLIENT_ID: process.env.GOOGLE_CLIENT_ID,
    GET_USER_URL: process.env.GET_USER_URL,
    LOGIN_URL: process.env.LOGIN_URL,
    LOGOUT_URL: process.env.LOGOUT_URL,
  },
  trailingSlash: true,
  images: { unoptimized: true },
  skipTrailingSlashRedirect: true,
  webpack: (config, { isServer }) => {
    config.resolve.fallback = { fs: false };
    // Avoid bundling Node-only server deps (they pull thousands of modules).
    if (isServer) {
      config.externals = config.externals || [];
      config.externals.push(...['sequelize', 'mysql2', 'google-auth-library', 'multer', 'next-connect']);
    }
    return config;
  },
};

const withTM = require('next-transpile-modules')(['@berryv/g2-react', '@antv/g2', '@antv/g6', '@antv/graphin']);

module.exports = withTM({
  ...nextConfig,
});
