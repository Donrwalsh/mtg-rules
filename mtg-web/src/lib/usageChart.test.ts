import { describe, expect, it } from 'vitest';
import { barHeight, chartScale } from './usageChart';

describe('chartScale', () => {
  it('tops out at the budget when every day is under it', () => {
    expect(chartScale([0.2, 0.4], 1)).toEqual({ max: 1, ticks: [0, 0.5, 1] });
  });

  it('grows to fit a day over budget', () => {
    expect(chartScale([0.2, 1.5], 1)).toEqual({ max: 1.5, ticks: [0, 0.75, 1.5] });
  });

  it('never divides by zero', () => {
    expect(chartScale([0, 0], 0).max).toBeGreaterThan(0);
  });
});

describe('barHeight', () => {
  it('scales to the chart height', () => {
    expect(barHeight(0.5, 1, 220)).toBe(110);
    expect(barHeight(0, 1, 220)).toBe(0);
  });
});
