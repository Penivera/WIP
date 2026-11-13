/**
 * Name validation. Mirrors `validate_name_checked` in contracts/registry/src/lib.rs.
 * Keep the two in step: the contract is the authority.
 */

export const NAME_MIN = 3;
export const NAME_MAX = 32;

export const RESERVED_NAMES: readonly string[] = [
  "admin", "root", "system", "near", "uni", "registry", "registrar", "www", "api", "app",
  "support", "help", "security", "official", "mail", "null",
];

/** Suffix the contract strips today. Not final, so callers can pass their own. */
export const DEFAULT_SUFFIX = ".uni";

export type NameResult = { ok: true; name: string } | { ok: false; reason: string };

// The contract trims with Rust's `str::trim` and lowercases ASCII only.
function asciiLower(s: string): string {
  return s.replace(/[A-Z]/g, (c) => c.toLowerCase());
}

export function normalizeName(raw: string, suffix: string = DEFAULT_SUFFIX): NameResult {
  const lowered = asciiLower(raw.trim());
  const label = suffix && lowered.endsWith(suffix) ? lowered.slice(0, -suffix.length) : lowered;
  if (label.length < NAME_MIN || label.length > NAME_MAX) {
    return { ok: false, reason: `length must be ${NAME_MIN}..=${NAME_MAX}` };
  }
  if (!/^[a-z0-9-]+$/.test(label)) {
    return { ok: false, reason: "only a-z, 0-9 and '-' allowed" };
  }
  if (label.startsWith("-") || label.endsWith("-") || label.includes("--")) {
    return { ok: false, reason: "bad '-' placement" };
  }
  if (RESERVED_NAMES.includes(label)) {
    return { ok: false, reason: "reserved name" };
  }
  return { ok: true, name: label };
}

/** Strips the suffix from a name the contract returned (e.g. from `records_of`). */
export function stripSuffix(name: string, suffix: string = DEFAULT_SUFFIX): string {
  return suffix && name.endsWith(suffix) ? name.slice(0, -suffix.length) : name;
}
