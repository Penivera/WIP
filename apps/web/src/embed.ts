/**
 * Embeddable entry point. The host page calls `UniversalName.open()` and the
 * widget appears as a popup over the page: no redirect, no change to the
 * host's URL, and its styles stay inside a shadow root.
 *
 *   <script src="universal-name.js"></script>
 *   <script>UniversalName.open({ name: "peni" })</script>
 */
import { createApp, type App } from "vue";
import Widget from "./Widget.vue";
import { configure, type Settings } from "./lib/config";
import { nav } from "./lib/nav";
import css from "./widget.css?inline";

export interface OpenOptions extends Partial<Settings> {
  /** Name to pre-fill on the pay screen. */
  name?: string;
  /** Which flow to start on. Default: "pay". */
  start?: "pay" | "names";
  /** Dim the page behind the popup. Default: true. */
  backdrop?: boolean;
  /** Called after the popup is closed. */
  onClose?: () => void;
}

let host: HTMLElement | undefined;
let app: App | undefined;

export function close(): void {
  app?.unmount();
  host?.remove();
  app = undefined;
  host = undefined;
}

export function open(options: OpenOptions = {}): void {
  close();
  const { name, start, backdrop, onClose, ...overrides } = options;
  configure(overrides);
  nav.reset(start === "names" ? "names" : "pay");

  host = document.createElement("div");
  host.setAttribute("data-universal-name", "");
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  const mount = document.createElement("div");
  root.append(style, mount);
  document.body.append(host);

  app = createApp(Widget, {
    initialName: name ?? "",
    backdrop: backdrop ?? true,
    onClose: () => {
      close();
      onClose?.();
    },
  });
  app.mount(mount);
}

export const isOpen = () => Boolean(host);
