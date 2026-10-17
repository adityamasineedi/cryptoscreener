import userEvent, { PointerEventsCheckLevel } from "@testing-library/user-event";

/**
 * userEvent for the large Backtest screen tests.
 *
 * - ``pointerEventsCheck: Never``: by default every pointer action walks the
 *   target's ancestors through jsdom's getComputedStyle to look for
 *   ``pointer-events: none``. The app's styles are Tailwind classes, which
 *   jsdom never loads, so the check cannot fail here; it only costs
 *   ~100-250 ms per click on BacktestPanel.
 * - ``delay: null``: no macrotask yield between the keystrokes/pointer steps
 *   of one action (React updates still flush through Testing Library's act()).
 */
export function setupUser() {
  return userEvent.setup({ delay: null, pointerEventsCheck: PointerEventsCheckLevel.Never });
}
