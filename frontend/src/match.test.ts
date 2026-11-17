import { describe, expect, it } from 'vitest'
import { matchPercent, matchStrength } from './match'

describe('matchPercent', () => {
  it('turns a 0 to 1 score into a whole percentage', () => {
    expect(matchPercent(0.996)).toBe(100)
    expect(matchPercent(0.4249)).toBe(42)
    expect(matchPercent(0)).toBe(0)
  })

  it('keeps out-of-range and missing scores within 0 to 100', () => {
    expect(matchPercent(1.7)).toBe(100)
    expect(matchPercent(-0.2)).toBe(0)
    expect(matchPercent(Number.NaN)).toBe(0)
  })
})

describe('matchStrength', () => {
  it('splits at 70 and 30 percent', () => {
    expect(matchStrength(100)).toBe('strong')
    expect(matchStrength(70)).toBe('strong')
    expect(matchStrength(69)).toBe('partial')
    expect(matchStrength(30)).toBe('partial')
    expect(matchStrength(29)).toBe('weak')
    expect(matchStrength(0)).toBe('weak')
  })
})
