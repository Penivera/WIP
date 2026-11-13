/** Entry for the single-file build (`pnpm build:embed`), exposed as `window.UniversalName`. */
export { open, close, isOpen } from "./embed";
export type { OpenOptions } from "./embed";
