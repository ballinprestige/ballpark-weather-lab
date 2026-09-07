import type { GameWeather } from './types';
import { normalizeDegrees } from './geometry';

export interface ParkWindVector {
  /** Meteorological report: the direction the wind comes from. */
  fromDeg: number;
  /** Physical travel direction after converting meteorological FROM to TO. */
  toDeg: number;
  /** Clockwise angle from the park's home-to-centre-field axis. */
  relativeDeg: number;
  carryMph: number;
  crossMph: number;
  svgX: number;
  svgY: number;
}

export function parkWindVector(weather: GameWeather, cfAzimuth: number): ParkWindVector | null {
  if (!Number.isFinite(weather.wind_speed_mph) || weather.wind_speed_mph < 0 || weather.wind_direction_deg === null || !Number.isFinite(weather.wind_direction_deg)) return null;
  const fromDeg = normalizeDegrees(weather.wind_direction_deg);
  const toDeg = normalizeDegrees(fromDeg + 180);
  const relativeDeg = normalizeDegrees(toDeg - cfAzimuth);
  const radians = relativeDeg * Math.PI / 180;
  const carryMph = weather.wind_speed_mph * Math.cos(radians);
  const crossMph = weather.wind_speed_mph * Math.sin(radians);
  return {
    fromDeg,
    toDeg,
    relativeDeg,
    carryMph: Object.is(carryMph, -0) ? 0 : carryMph,
    crossMph: Object.is(crossMph, -0) ? 0 : crossMph,
    svgX: Math.sin(radians),
    svgY: -Math.cos(radians)
  };
}

export function isOutdoorWindSuppressed(weather: GameWeather): boolean {
  return weather.dome_active || weather.roof_state === 'fixed-roof';
}
