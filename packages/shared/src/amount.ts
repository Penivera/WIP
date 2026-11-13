/** Decimal text <-> base-unit integers, without floating point. */

export function parseUnits(amount: string, decimals: number): bigint {
  const text = amount.trim();
  if (!/^\d+(\.\d+)?$/.test(text)) throw new Error("amount must be a positive decimal number");
  const [whole = "0", frac = ""] = text.split(".");
  if (frac.length > decimals) throw new Error(`amount has more than ${decimals} decimal places`);
  return BigInt(whole + frac.padEnd(decimals, "0"));
}

export function formatUnits(value: bigint | string, decimals: number): string {
  const s = BigInt(value).toString().padStart(decimals + 1, "0");
  const whole = s.slice(0, s.length - decimals);
  const frac = s.slice(s.length - decimals).replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : whole;
}
