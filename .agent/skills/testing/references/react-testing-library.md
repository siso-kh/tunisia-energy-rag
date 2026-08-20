# React Testing Library Reference

## Setup & Detection
- Check for existing test setup: `jest.config.js` / `vitest.config.ts`, `setupTests.ts`, and whether `@testing-library/jest-dom` matchers are already imported globally.
- Match existing conventions: co-located `Component.test.tsx` vs a `__tests__/` folder, existing custom render wrappers (e.g., providers already wrapped in a `test-utils.tsx`).

## Query Priority (use in this order)
1. `getByRole` (with `name` option) — matches how users/assistive tech perceive the UI. Default choice.
2. `getByLabelText` — for form fields.
3. `getByPlaceholderText`, `getByText` — reasonable fallbacks.
4. `getByTestId` — last resort only, when no accessible query works (e.g., a decorative element with no semantic role).
- Avoid `container.querySelector`, class-name selectors, or snapshotting raw DOM structure — these couple tests to implementation and break on styling refactors.

## Interactions
- Use `@testing-library/user-event` (not `fireEvent`) for simulating clicks, typing, and keyboard nav — it fires the full realistic event sequence.
- Always `await` user-event calls (`await user.click(...)`) — they're async by default in recent versions.

## Async UI
- Use `findBy*` queries (return a promise) for elements that appear after an async action, instead of `waitFor(() => getByText(...))` boilerplate.
- Use `waitFor` for asserting a side effect (e.g., a mock was called) rather than for querying an element.
- Never use `act()` manually unless RTL explicitly tells you to — wrapping interactions in `user-event`/`findBy*` already handles it.

## Mocking
- Mock network calls at the boundary: `msw` (Mock Service Worker) is preferred over mocking `fetch`/`axios` directly, since it intercepts at the network layer and tests real request/response handling.
- Mock child components only when they're heavy/irrelevant to the unit under test (e.g., a chart library) — don't mock components that are part of what you're testing.

## Common Cases to Cover
- Loading state → success state → error state, for anything async.
- Form validation: empty submit, invalid input, successful submit — assert on user-visible feedback, not internal state.
- Conditional rendering branches (empty list, single item, many items).
- Keyboard accessibility for interactive elements (Enter/Space activation, focus order) where relevant.

## Common Pitfalls to Avoid
- Don't assert on implementation details like component state or prop values — assert on what's rendered.
- Don't use snapshot tests for dynamic/data-driven output — reserve snapshots for stable, rarely-changing structural components, and review diffs critically rather than blindly updating.
- Clean up between tests: RTL auto-cleans in most setups, but verify `afterEach(cleanup)` isn't duplicated or missing if you see cross-test pollution.