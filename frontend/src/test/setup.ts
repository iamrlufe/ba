import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Recent Node versions (>=22) ship their own experimental, flag-gated global
// `localStorage`/`sessionStorage` getters that throw/no-op without
// `--localstorage-file`. Vitest's jsdom environment sees those keys already
// present on globalThis and (per its populateGlobal key-filtering) skips
// wiring up jsdom's real, working implementation over them -- leaving
// `window.localStorage` silently `undefined` in tests. Pull jsdom's actual
// Storage instances (created by the environment's own `JSDOM` instance,
// exposed as `globalThis.jsdom`) and install them explicitly so app code
// that reads bare `localStorage`/`sessionStorage`/`window.localStorage` works
// exactly as it would in a real browser.
const jsdomWindow = (globalThis as unknown as { jsdom?: { window: Window } }).jsdom?.window;
if (jsdomWindow) {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get: () => jsdomWindow.localStorage,
  });
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    get: () => jsdomWindow.sessionStorage,
  });
}

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

// jsdom doesn't implement these, but Radix UI (Select/Dialog/etc.) needs them
// to run its pointer-interaction and scroll logic without throwing.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

if (typeof window.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

if (typeof window.matchMedia === "undefined") {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}
