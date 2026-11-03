export function sequenceCountdown(value: number, timing: "beats" | "seconds", step = 1): string {
  const remaining = Math.max(0, value);
  if (timing === "seconds") return `${remaining.toFixed(1)} s`;
  const quantum = step === 1 ? 1 : 0.5;
  const ticks = Math.ceil(remaining / quantum - 1e-9) * quantum;
  return `${Math.max(0, ticks).toFixed(quantum === 1 ? 0 : 1)} beats`;
}
