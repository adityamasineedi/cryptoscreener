import "@testing-library/jest-dom/vitest";
import { beforeAll } from "vitest";

// One-time jsdom warm-up per test file. jsdom builds its default stylesheet on
// the first getComputedStyle call (~0.5 s idle, several seconds when the
// machine is loaded), and Testing Library builds its ARIA role tables on the
// first role query. Without this the first test of every jsdom file that
// renders anything pays that cost inside its own 5 s timeout, which is why
// "1. BTC v1 risk rendering", "renders four shards ..." and "renders a BULLISH
// book ..." were the ones that timed out. Node-environment files skip it.
beforeAll(async () => {
  if (typeof document === "undefined") return;
  const { queryAllByRole } = await import("@testing-library/react");
  const host = document.createElement("div");
  host.innerHTML = '<button type="button">warm</button><table><tbody><tr><td>x</td></tr></tbody></table>';
  document.body.appendChild(host);
  try {
    getComputedStyle(host);
    queryAllByRole(host, "button", { name: "warm" });
    queryAllByRole(host, "row");
  } finally {
    host.remove();
  }
});
