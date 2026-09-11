import { describe, expect, it } from 'vitest';
import { bytes, clsx, duration, hexToRgbTriplet, relativeTime, titleCase } from '@/lib/format';

describe('format helpers', () => {
  it('formats byte sizes with sensible precision', () => {
    expect(bytes(0)).toBe('0 B');
    expect(bytes(null)).toBe('0 B');
    expect(bytes(512)).toBe('512 B');
    expect(bytes(1536)).toBe('1.5 KB');
    expect(bytes(12 * 1024 * 1024)).toBe('12 MB');
    expect(bytes(5 * 1024 ** 3)).toBe('5 GB');
  });

  it('formats durations', () => {
    expect(duration(null)).toBe('—');
    expect(duration(0)).toBe('0s');
    expect(duration(45)).toBe('45s');
    expect(duration(125)).toBe('2m 05s');
  });

  it('formats relative time', () => {
    expect(relativeTime(null)).toBe('—');
    expect(relativeTime('not-a-date')).toBe('—');
    expect(relativeTime(new Date(Date.now() - 5000).toISOString())).toBe('just now');
    expect(relativeTime(new Date(Date.now() - 2 * 3_600_000).toISOString())).toBe('2h ago');
  });

  it('title-cases slugs', () => {
    expect(titleCase('text-to-image')).toBe('Text To Image');
    expect(titleCase('background_remove')).toBe('Background Remove');
  });

  it('converts brand colours to CSS triplets with a safe fallback', () => {
    expect(hexToRgbTriplet('#7C5CFF')).toBe('124 92 255');
    expect(hexToRgbTriplet('#fff')).toBe('255 255 255');
    expect(hexToRgbTriplet('nonsense')).toBe('124 92 255');
  });

  it('joins class names defensively', () => {
    expect(clsx('a', false, null, undefined, 'b')).toBe('a b');
  });
});
