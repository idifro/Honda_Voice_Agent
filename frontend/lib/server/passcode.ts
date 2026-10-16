import { createHash, timingSafeEqual } from 'node:crypto';

/** Constant-time passcode check: both sides are hashed first, so length differences don't leak either. */
export function passcodeMatches(given: unknown, expected: string): boolean {
  if (
    typeof given !== 'string' ||
    given.length === 0 ||
    given.length > 200 ||
    expected.length === 0
  ) {
    return false;
  }
  const a = createHash('sha256').update(given).digest();
  const b = createHash('sha256').update(expected).digest();
  return timingSafeEqual(a, b);
}
