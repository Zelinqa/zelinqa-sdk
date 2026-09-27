import { defineConfig } from "vitest/config";

/**
 * Live suite against the staging API. Opt-in: every test self-skips unless
 * `ZELINQA_LIVE=1`. Runs single-threaded and sequentially to avoid fixture
 * contention, with a long timeout for the compilation wait.
 */
export default defineConfig({
  test: {
    globals: true,
    include: ["typescript/tests/live/**/*.test.ts"],
    testTimeout: 1_200_000,
    hookTimeout: 1_200_000,
    fileParallelism: false,
    sequence: { concurrent: false },
    pool: "forks",
    poolOptions: { forks: { singleFork: true } },
    retry: 0,
  },
});
