import { describe, expect, it } from 'vitest';
import type { GameWeather } from './types';
import { isOutdoorWindSuppressed, parkWindVector } from './wind';

const weather = (direction: number | null, speed = 10): GameWeather => ({
  game_pk: 1, state: 'verified', source: 'test', basis: 'forecast', reason: null, valid_at: null, fetched_at: null,
  temperature_f: 70, humidity_pct: 50, wind_speed_mph: speed, wind_direction_deg: direction,
  wind_carry_mph: 0, wind_cross_mph: 0, air_density_index: 100, pressure_hpa: 1013, dome_active: false, roof_state: 'open-air'
});

describe('park wind convention', () => {
  it('converts meteorological FROM into an outward TO vector relative to centre field', () => {
    const vector = parkWindVector(weather(180), 0)!;
    expect(vector.toDeg).toBe(0);
    expect(vector.carryMph).toBeCloseTo(10);
    expect(vector.crossMph).toBeCloseTo(0);
    expect(vector.svgY).toBeCloseTo(-1);
  });

  it('keeps left and right pure crosswinds visible and signed', () => {
    expect(parkWindVector(weather(270), 0)!.crossMph).toBeCloseTo(10);
    expect(parkWindVector(weather(90), 0)!.crossMph).toBeCloseTo(-10);
  });

  it('normalizes 0 and 360 and never turns a missing direction into calm', () => {
    expect(parkWindVector(weather(0), 20)!.toDeg).toBe(180);
    expect(parkWindVector(weather(360), 20)!.toDeg).toBe(180);
    expect(parkWindVector(weather(null), 0)).toBeNull();
  });

  it('treats zero speed as calm and a fixed roof as no outdoor effect', () => {
    expect(parkWindVector(weather(20, 0), 0)!.carryMph).toBe(0);
    const roofed = weather(20); roofed.roof_state = 'fixed-roof';
    expect(isOutdoorWindSuppressed(roofed)).toBe(true);
  });
});
