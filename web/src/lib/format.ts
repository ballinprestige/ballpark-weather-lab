import type { BallparkGame, GameWeather, HealthLane, JsonRecord } from './types';
import { isReadyState } from './validate';

const dateFormatter = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const timeFormatter = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
const timestampFormatter = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
const integerFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const decimalFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 });

export function formatDate(date: string): string {
  return dateFormatter.format(new Date(`${date}T12:00:00Z`));
}

export function formatTime(timestamp: string | null | undefined): string {
  if (!timestamp) return 'Time not reported';
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : timeFormatter.format(date);
}

export function formatTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) return 'Not reported';
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : timestampFormatter.format(date);
}

export function formatFactor(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(3);
}

export function formatDelta(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined) return '—';
  const rounded = Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value;
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}`;
}

export function formatNumber(value: unknown): string {
  if (typeof value !== 'number') return String(value ?? 'Not reported');
  return Number.isInteger(value) ? integerFormatter.format(value) : decimalFormatter.format(value);
}

export function humanizeKey(key: string): string {
  const aliases: Record<string, string> = {
    evidence_games: 'Historical evidence games',
    held_out_rmse: 'Held-out RMSE',
    batter_profiles: 'Batter profiles',
    trajectory_entries: 'Trajectory lookup entries',
    park_geometries: 'Park geometries',
    stadium_geometries: 'Park geometries',
    model_version: 'Model version',
    training_period: 'Training period',
    geometry_version: 'Geometry version'
  };
  return aliases[key] ?? key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function shortHash(hash: string | null | undefined): string {
  return hash ? `${hash.slice(0, 12)}…` : 'Unavailable';
}

export function healthState(lane: string | HealthLane): string {
  if (typeof lane === 'string') return lane;
  return lane.state ?? lane.status ?? 'unknown';
}

export function healthReason(lane: string | HealthLane): string | null {
  if (typeof lane === 'string') return null;
  return lane.reason ?? lane.detail ?? null;
}

export function stateTone(state: string | null | undefined): 'good' | 'hold' | 'bad' | 'neutral' {
  const normalized = (state ?? '').toLowerCase();
  if (isReadyState(normalized)) return 'good';
  if (['missing', 'pending', 'held', 'partial', 'degraded', 'unavailable', 'not_available', 'not_yet_available', 'not yet available'].includes(normalized)) return 'hold';
  if (['error', 'invalid', 'failed', 'stale'].includes(normalized)) return 'bad';
  return 'neutral';
}

export function isGameHeld(game: BallparkGame): boolean {
  return !isReadyState(game.weather.state) || !isReadyState(game.factors.state);
}

export function gameHoldReason(game: BallparkGame): string {
  if (!isReadyState(game.weather.state)) return game.weather.reason ?? 'Game-hour weather is not available, so weather-adjusted factors are held.';
  if (!isReadyState(game.factors.state)) return game.factors.reason ?? 'Park-factor output is not available for this game.';
  return '';
}

export function windSpeed(weather: GameWeather): number {
  return weather.wind_speed_mph;
}

export function windDirectionDegrees(weather: GameWeather): number | null {
  return weather.wind_direction_deg;
}

export function windLabel(weather: GameWeather): string {
  const degrees = weather.wind_direction_deg;
  if (degrees === null) return `${weather.wind_speed_mph.toFixed(0)} mph · direction not reported`;
  const cardinal = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(degrees / 45) % 8];
  return `${weather.wind_speed_mph.toFixed(0)} mph from ${cardinal}`;
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not reported';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return formatNumber(value);
  if (Array.isArray(value)) return value.map(displayValue).join('; ');
  if (typeof value === 'object') {
    return Object.entries(value as JsonRecord).map(([key, item]) => `${humanizeKey(key)}: ${displayValue(item)}`).join(' · ');
  }
  return String(value);
}

export function pitcherName(game: BallparkGame, side: 'home' | 'away'): string {
  return game[`${side}_pitcher`] || 'Not announced';
}

const TEAM_NAMES: Record<string, string> = {
  ARI: 'Arizona Diamondbacks', ATL: 'Atlanta Braves', BAL: 'Baltimore Orioles', BOS: 'Boston Red Sox',
  CHC: 'Chicago Cubs', CIN: 'Cincinnati Reds', CLE: 'Cleveland Guardians', COL: 'Colorado Rockies',
  CWS: 'Chicago White Sox', DET: 'Detroit Tigers', HOU: 'Houston Astros', KC: 'Kansas City Royals',
  LAA: 'Los Angeles Angels', LAD: 'Los Angeles Dodgers', MIA: 'Miami Marlins', MIL: 'Milwaukee Brewers',
  MIN: 'Minnesota Twins', NYM: 'New York Mets', NYY: 'New York Yankees', OAK: 'Athletics',
  PHI: 'Philadelphia Phillies', PIT: 'Pittsburgh Pirates', SD: 'San Diego Padres', SEA: 'Seattle Mariners',
  SF: 'San Francisco Giants', STL: 'St. Louis Cardinals', TB: 'Tampa Bay Rays', TEX: 'Texas Rangers',
  TOR: 'Toronto Blue Jays', WSH: 'Washington Nationals'
};

export function teamLabel(code: string): string {
  return TEAM_NAMES[code] ?? code;
}
