// @ts-check
import { defineConfig } from "astro/config";

/**
 * Static output only. The site reads the committed `data/` artifacts at BUILD time and ships
 * plain files — no server, no API calls at runtime, and nothing that could hit data.gov.my
 * from a visitor's browser.
 *
 * `site` is used for canonical URLs and the sitemap. It is a placeholder until the Cloudflare
 * Pages project exists; override with the `SITE` environment variable in CI.
 */
export default defineConfig({
  site: process.env.SITE ?? "https://quality-of-life-my.pages.dev",
  output: "static",
  build: {
    // One stylesheet rather than several small ones: fewer round trips, and the total is
    // small enough that inlining everything would be worse for caching.
    inlineStylesheets: "auto",
  },
  vite: {
    build: {
      // Observable Plot is only pulled in by chart islands, so it stays out of the critical
      // path. This just keeps the warning quiet for the chunk that does include it.
      chunkSizeWarningLimit: 900,
    },
  },
});
