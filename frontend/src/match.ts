/**
 * How well a source matches the question, from the reranker's score.
 *
 * The reranker reads the question and the passage together and returns the probability that
 * the passage answers the question (0 to 1), so it reads directly as a percentage.
 */

export type MatchStrength = 'strong' | 'partial' | 'weak'

/** The score as a whole percentage, 0 to 100. */
export function matchPercent(score: number): number {
  if (!Number.isFinite(score)) return 0
  return Math.round(Math.min(Math.max(score, 0), 1) * 100)
}

export function matchStrength(percent: number): MatchStrength {
  if (percent >= 70) return 'strong'
  if (percent >= 30) return 'partial'
  return 'weak'
}

export const MATCH_LABEL: Record<MatchStrength, string> = {
  strong: 'Strong match',
  partial: 'Partial match',
  weak: 'Weak match',
}
