// The spend chart's y scale: $0 up to the budget, or higher when a day
// went over it, with a midline.
export function chartScale(values: number[], budget: number): { max: number; ticks: number[] } {
  const max = Math.max(budget, ...values, 0) || 1;
  return { max, ticks: [0, max / 2, max] };
}

export function barHeight(value: number, max: number, height: number): number {
  return Math.round((value / max) * height);
}
