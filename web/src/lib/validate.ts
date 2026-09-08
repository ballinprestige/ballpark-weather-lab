import type {
  ApproachC,
  ArchiveIndex,
  BallparkGame,
  BallparkPayload,
  GameExchangeMarket,
  GameFactors,
  GameLineup,
  GameOdds,
  GameTrajectory,
  GameWeather,
  GeometryArtifact,
  JsonRecord,
  PublicationHealth,
  PublicationStatus,
  ReleasePointer,
  TrajectoryArc,
  TrajectoryPoint
} from './types';

export class ArtifactValidationError extends Error {
  constructor(message: string) {
    super(`Artifact validation failed: ${message}`);
    this.name = 'ArtifactValidationError';
  }
}

function fail(path: string, expectation: string): never {
  throw new ArtifactValidationError(`${path} ${expectation}.`);
}

function objectAt(value: unknown, path: string): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(path, 'must be an object');
  return value as JsonRecord;
}

function arrayAt(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) fail(path, 'must be an array');
  return value;
}

function stringAt(value: unknown, path: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== 'string' || (!nullable && value.trim() === '')) {
    fail(path, nullable ? 'must be a string or null' : 'must be a non-empty string');
  }
  return value as string;
}

function nullableStringAt(value: unknown, path: string): string | null {
  return stringAt(value, path, true);
}

function numberAt(
  value: unknown,
  path: string,
  minimum = Number.NEGATIVE_INFINITY,
  maximum = Number.POSITIVE_INFINITY
): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) fail(path, 'must be a finite number');
  if (value < minimum || value > maximum) fail(path, `must be between ${minimum} and ${maximum}`);
  return value;
}

function optionalNumberAt(
  row: JsonRecord,
  key: string,
  path: string,
  minimum: number,
  maximum: number
): number | undefined {
  return key in row ? numberAt(row[key], `${path}.${key}`, minimum, maximum) : undefined;
}

function integerAt(value: unknown, path: string, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  const number = numberAt(value, path, minimum, maximum);
  if (!Number.isInteger(number)) fail(path, 'must be an integer');
  return number;
}

function booleanAt(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') fail(path, 'must be a boolean');
  return value;
}

function enumAt<T extends string>(value: unknown, path: string, allowed: readonly T[]): T {
  if (typeof value !== 'string' || !allowed.includes(value as T)) {
    fail(path, `must be one of ${allowed.join(', ')}`);
  }
  return value as T;
}

function isoDateAt(value: unknown, path: string): string {
  const date = stringAt(value, path) as string;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) fail(path, 'must be an ISO calendar date');
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    fail(path, 'must be a real ISO calendar date');
  }
  return date;
}

function timestampAt(value: unknown, path: string, nullable = false): string | null {
  const timestamp = stringAt(value, path, nullable);
  if (timestamp === null) return null;
  const pattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
  if (!pattern.test(timestamp) || Number.isNaN(Date.parse(timestamp))) {
    fail(path, nullable ? 'must be an RFC 3339 timestamp or null' : 'must be an RFC 3339 timestamp');
  }
  return timestamp;
}

function validateWeather(value: unknown, path: string): GameWeather {
  const row = objectAt(value, path);
  const direction = row.wind_direction_deg === null
    ? null
    : numberAt(row.wind_direction_deg, `${path}.wind_direction_deg`, 0, 360);
  return {
    game_pk: integerAt(row.game_pk, `${path}.game_pk`, 1),
    state: enumAt(row.state, `${path}.state`, ['verified', 'degraded']),
    source: stringAt(row.source, `${path}.source`) as string,
    basis: enumAt(row.basis, `${path}.basis`, ['forecast', 'observation', 'indoor', 'neutral']),
    reason: nullableStringAt(row.reason, `${path}.reason`),
    valid_at: timestampAt(row.valid_at, `${path}.valid_at`, true),
    fetched_at: timestampAt(row.fetched_at, `${path}.fetched_at`, true),
    temperature_f: numberAt(row.temperature_f, `${path}.temperature_f`, -80, 150),
    humidity_pct: numberAt(row.humidity_pct, `${path}.humidity_pct`, 0, 100),
    wind_speed_mph: numberAt(row.wind_speed_mph, `${path}.wind_speed_mph`, 0, 250),
    wind_direction_deg: direction,
    wind_carry_mph: numberAt(row.wind_carry_mph, `${path}.wind_carry_mph`, -250, 250),
    wind_cross_mph: numberAt(row.wind_cross_mph, `${path}.wind_cross_mph`, -250, 250),
    air_density_index: numberAt(row.air_density_index, `${path}.air_density_index`, 0, 200),
    pressure_hpa: numberAt(row.pressure_hpa, `${path}.pressure_hpa`, 500, 1200),
    dome_active: booleanAt(row.dome_active, `${path}.dome_active`),
    roof_state: enumAt(row.roof_state, `${path}.roof_state`, ['open-air', 'fixed-roof', 'unconfirmed', 'unknown'])
  };
}

function validateFactors(value: unknown, path: string): GameFactors {
  const row = objectAt(value, path);
  return {
    state: enumAt(row.state, `${path}.state`, ['modeled', 'held']),
    reason: nullableStringAt(row.reason, `${path}.reason`),
    seasonal_pf_runs: numberAt(row.seasonal_pf_runs, `${path}.seasonal_pf_runs`, 0.1, 5),
    seasonal_pf_hr: numberAt(row.seasonal_pf_hr, `${path}.seasonal_pf_hr`, 0.1, 5),
    weather_multiplier_runs: numberAt(row.weather_multiplier_runs, `${path}.weather_multiplier_runs`, 0.7, 1.4),
    weather_multiplier_hr: numberAt(row.weather_multiplier_hr, `${path}.weather_multiplier_hr`, 0.7, 1.4),
    game_pf_runs: numberAt(row.game_pf_runs, `${path}.game_pf_runs`, 0.1, 5),
    game_pf_hr: numberAt(row.game_pf_hr, `${path}.game_pf_hr`, 0.1, 5),
    weather_delta_runs: numberAt(row.weather_delta_runs, `${path}.weather_delta_runs`, -5, 5),
    weather_delta_hr: numberAt(row.weather_delta_hr, `${path}.weather_delta_hr`, -5, 5),
    hr_baseline_as_of: isoDateAt(row.hr_baseline_as_of, `${path}.hr_baseline_as_of`)
  };
}

function validateLineup(value: unknown, path: string): GameLineup {
  const row = objectAt(value, path);
  return {
    state: enumAt(row.state, `${path}.state`, ['confirmed', 'partial', 'not_yet_available', 'unavailable']),
    reason: nullableStringAt(row.reason, `${path}.reason`),
    observed_at: timestampAt(row.observed_at, `${path}.observed_at`, true),
    home_count: integerAt(row.home_count, `${path}.home_count`, 0, 9),
    away_count: integerAt(row.away_count, `${path}.away_count`, 0, 9)
  };
}

function validateApproachC(value: unknown, path: string): ApproachC {
  const row = objectAt(value, path);
  if (row.used_in_headline !== false) fail(`${path}.used_in_headline`, 'must remain false');
  return {
    state: enumAt(row.state, `${path}.state`, ['experimental', 'not_available']),
    reason: nullableStringAt(row.reason, `${path}.reason`),
    used_in_headline: false,
    method: stringAt(row.method, `${path}.method`) as string,
    home_hr_index: optionalNumberAt(row, 'home_hr_index', path, 0.3, 3),
    away_hr_index: optionalNumberAt(row, 'away_hr_index', path, 0.3, 3),
    home_minus_away: optionalNumberAt(row, 'home_minus_away', path, -2.7, 2.7),
    home_profile_coverage: 'home_profile_coverage' in row
      ? integerAt(row.home_profile_coverage, `${path}.home_profile_coverage`, 0, 9)
      : undefined,
    away_profile_coverage: 'away_profile_coverage' in row
      ? integerAt(row.away_profile_coverage, `${path}.away_profile_coverage`, 0, 9)
      : undefined
  };
}

function validatePoint(value: unknown, path: string): TrajectoryPoint {
  if (!Array.isArray(value) || value.length !== 2) fail(path, 'must contain exactly two coordinates');
  return [numberAt(value[0], `${path}[0]`), numberAt(value[1], `${path}[1]`)];
}

function validateTrajectoryArc(value: unknown, path: string): TrajectoryArc {
  const row = objectAt(value, path);
  const neutral = arrayAt(row.neutral_points_ft, `${path}.neutral_points_ft`)
    .map((point, index) => validatePoint(point, `${path}.neutral_points_ft[${index}]`));
  const weather = arrayAt(row.weather_points_ft, `${path}.weather_points_ft`)
    .map((point, index) => validatePoint(point, `${path}.weather_points_ft[${index}]`));
  if (neutral.length < 2 || neutral.length > 20 || weather.length < 2 || weather.length > 20) {
    fail(path, 'must contain between 2 and 20 points in each trajectory');
  }
  return {
    archetype: stringAt(row.archetype, `${path}.archetype`) as string,
    exit_velocity_mph: numberAt(row.exit_velocity_mph, `${path}.exit_velocity_mph`, 0, 150),
    launch_angle_deg: numberAt(row.launch_angle_deg, `${path}.launch_angle_deg`, -90, 90),
    spray_angle_deg: numberAt(row.spray_angle_deg, `${path}.spray_angle_deg`, -90, 90),
    weather_distance_ft: numberAt(row.weather_distance_ft, `${path}.weather_distance_ft`, 0, 1000),
    neutral_distance_ft: numberAt(row.neutral_distance_ft, `${path}.neutral_distance_ft`, 0, 1000),
    carry_delta_ft: numberAt(row.carry_delta_ft, `${path}.carry_delta_ft`, -1000, 1000),
    weather_points_ft: weather,
    neutral_points_ft: neutral
  };
}

function validateTrajectory(value: unknown, path: string): GameTrajectory {
  const row = objectAt(value, path);
  const arcs = arrayAt(row.arcs, `${path}.arcs`)
    .map((arc, index) => validateTrajectoryArc(arc, `${path}.arcs[${index}]`));
  if (arcs.length > 3) fail(`${path}.arcs`, 'must contain at most three trajectories');
  if (row.integration !== 'bounded Euler approximation') {
    fail(`${path}.integration`, 'must identify the bounded Euler approximation');
  }
  return {
    state: enumAt(row.state, `${path}.state`, ['available', 'held']),
    reason: nullableStringAt(row.reason, `${path}.reason`),
    integration: 'bounded Euler approximation',
    arcs
  };
}

function nullableNumberAt(value: unknown, path: string, minimum: number, maximum: number): number | null {
  return value === null ? null : numberAt(value, path, minimum, maximum);
}

function validateOdds(value: unknown, path: string, gamePk: number, gameDate: string): GameOdds {
  if (value === undefined) return {
    game_pk: gamePk, slate_date: gameDate, state: 'unavailable', reason: 'This legacy release predates quoted full-game totals.',
    provider_event_id: null, sport: 'MLB', market_type: 'total', period: 'full_game', eligibility: 'pregame',
    sportsbook_id: null, sportsbook_name: null, provider: 'Not recorded', source_url: null, line: null, over_price: null, under_price: null,
    source_updated_at: null, observed_at: null, raw_sha256: null, snapshot_id: null, source_schema_version: 'legacy-without-markets'
  };
  const row = objectAt(value, path);
  const state = enumAt(row.state, `${path}.state`, ['current', 'stale', 'observed_unknown_age', 'unavailable']);
  const market: GameOdds = {
    game_pk: integerAt(row.game_pk, `${path}.game_pk`, 1), slate_date: isoDateAt(row.slate_date, `${path}.slate_date`), state,
    reason: nullableStringAt(row.reason, `${path}.reason`), provider_event_id: nullableStringAt(row.provider_event_id, `${path}.provider_event_id`),
    sport: enumAt(row.sport, `${path}.sport`, ['MLB']), market_type: enumAt(row.market_type, `${path}.market_type`, ['total']),
    period: enumAt(row.period, `${path}.period`, ['full_game']), eligibility: enumAt(row.eligibility, `${path}.eligibility`, ['pregame', 'live', 'final']),
    sportsbook_id: nullableStringAt(row.sportsbook_id, `${path}.sportsbook_id`), sportsbook_name: nullableStringAt(row.sportsbook_name, `${path}.sportsbook_name`),
    provider: stringAt(row.provider, `${path}.provider`) as string, source_url: nullableStringAt(row.source_url, `${path}.source_url`),
    line: nullableNumberAt(row.line, `${path}.line`, 0, 40), over_price: row.over_price === null ? null : integerAt(row.over_price, `${path}.over_price`, -20000, 20000), under_price: row.under_price === null ? null : integerAt(row.under_price, `${path}.under_price`, -20000, 20000),
    source_updated_at: timestampAt(row.source_updated_at, `${path}.source_updated_at`, true), observed_at: timestampAt(row.observed_at, `${path}.observed_at`, true),
    raw_sha256: nullableStringAt(row.raw_sha256, `${path}.raw_sha256`), snapshot_id: nullableStringAt(row.snapshot_id, `${path}.snapshot_id`), source_schema_version: stringAt(row.source_schema_version, `${path}.source_schema_version`) as string
  };
  if (market.game_pk !== gamePk || market.slate_date !== gameDate) fail(path, 'must identify this exact scheduled game and slate date');
  const populated = [market.line, market.over_price, market.under_price, market.raw_sha256, market.snapshot_id].some((entry) => entry !== null);
  if (state !== 'unavailable' || populated) {
    const requiredObserved = [market.line, market.over_price, market.under_price, market.observed_at, market.raw_sha256, market.snapshot_id, market.sportsbook_id, market.sportsbook_name, market.provider_event_id];
    if (requiredObserved.some((entry) => entry === null)) {
      fail(path, 'quoted market must include the line, both prices, book, source/retrieval times, hash, and snapshot');
    }
    for (const [label, price] of [['over_price', market.over_price], ['under_price', market.under_price]] as const) {
      if (price === null || (price > -100 && price < 100)) fail(`${path}.${label}`, 'must be a supported American price (≤ -100 or ≥ +100)');
    }
    if (!/^[a-f0-9]{64}$/.test(market.raw_sha256 ?? '') || !/^[a-f0-9]{64}$/.test(market.snapshot_id ?? '')) {
      fail(path, 'quoted market provenance must use lowercase SHA-256 digests');
    }
    const observedMs = Date.parse(market.observed_at!);
    if (state === 'observed_unknown_age' && market.source_updated_at !== null) {
      fail(`${path}.source_updated_at`, 'must be null when the sportsbook quote update time is unknown');
    }
    if (market.source_updated_at === null) {
      if (state !== 'unavailable' && state !== 'observed_unknown_age') fail(path, 'current or stale quote must include a trustworthy source update time');
      return market;
    }
    const sourceMs = Date.parse(market.source_updated_at);
    if (sourceMs > observedMs + 5 * 60_000) fail(path, 'source update cannot be more than five minutes after retrieval');
    if (state === 'current' && (sourceMs < observedMs - 15 * 60_000 || sourceMs > observedMs)) {
      fail(path, 'current quote must be within the canonical 15-minute freshness window');
    }
  }
  return market;
}

function decimalAt(value: unknown, path: string, minimumExclusive = false): string | null {
  const decimal = nullableStringAt(value, path);
  if (decimal === null) return null;
  if (!/^\d+(?:\.\d+)?$/.test(decimal) || (minimumExclusive && Number(decimal) <= 0)) fail(path, 'must be a positive decimal string with source precision');
  return decimal;
}

function decimalEqual(left: string, right: string): boolean {
  const [leftWhole, leftFraction = ''] = left.split('.');
  const [rightWhole, rightFraction = ''] = right.split('.');
  const scale = Math.max(leftFraction.length, rightFraction.length);
  return BigInt(leftWhole + leftFraction.padEnd(scale, '0')) === BigInt(rightWhole + rightFraction.padEnd(scale, '0'));
}

function dollarsToCents(dollars: string): string {
  const [whole, fraction = ''] = dollars.split('.');
  const padded = fraction.padEnd(2, '0');
  return `${whole}${padded.slice(0, 2)}${padded.length > 2 ? `.${padded.slice(2)}` : ''}`;
}

function validateExchangeMarket(value: unknown, path: string, gamePk: number, gameDate: string, gameTime: string): GameExchangeMarket | undefined {
  if (value === undefined) return undefined;
  const row = objectAt(value, path);
  const state = enumAt(row.state, `${path}.state`, ['observed_unknown_age', 'unavailable']);
  const overDollars = decimalAt(row.over_ask_dollars, `${path}.over_ask_dollars`, true);
  const underDollars = decimalAt(row.under_ask_dollars, `${path}.under_ask_dollars`, true);
  const overCents = decimalAt(row.over_ask_cents, `${path}.over_ask_cents`, true);
  const underCents = decimalAt(row.under_ask_cents, `${path}.under_ask_cents`, true);
  const market: GameExchangeMarket = {
    game_pk: integerAt(row.game_pk, `${path}.game_pk`, 1), slate_date: isoDateAt(row.slate_date, `${path}.slate_date`), game_time: timestampAt(row.game_time, `${path}.game_time`, true), state,
    reason: nullableStringAt(row.reason, `${path}.reason`), failure_reason: nullableStringAt(row.failure_reason, `${path}.failure_reason`), game_phase: enumAt(row.game_phase, `${path}.game_phase`, ['pregame', 'in_progress', 'after_scheduled_start', 'final', 'unknown', 'unavailable']), provider: enumAt(row.provider, `${path}.provider`, ['Kalshi']), provider_url: stringAt(row.provider_url, `${path}.provider_url`) as string,
    event_ticker: nullableStringAt(row.event_ticker, `${path}.event_ticker`), market_ticker: nullableStringAt(row.market_ticker, `${path}.market_ticker`),
    market_type: enumAt(row.market_type, `${path}.market_type`, ['total']), period: enumAt(row.period, `${path}.period`, ['full_game']),
    quote_type: enumAt(row.quote_type, `${path}.quote_type`, ['contract_ask']), price_format: enumAt(row.price_format, `${path}.price_format`, ['contract_cents']),
    currency: enumAt(row.currency, `${path}.currency`, ['USD']), line: nullableNumberAt(row.line, `${path}.line`, 0, 40),
    over_ask_dollars: overDollars, under_ask_dollars: underDollars,
    over_ask_cents: overCents, under_ask_cents: underCents, source_updated_at: null,
    observed_at: timestampAt(row.observed_at, `${path}.observed_at`, true), raw_sha256: nullableStringAt(row.raw_sha256, `${path}.raw_sha256`),
    snapshot_id: nullableStringAt(row.snapshot_id, `${path}.snapshot_id`), active: booleanAt(row.active, `${path}.active`),
    condition: nullableStringAt(row.condition, `${path}.condition`), over_ask_size: decimalAt(row.over_ask_size, `${path}.over_ask_size`, true), under_ask_size: decimalAt(row.under_ask_size, `${path}.under_ask_size`, true), source_schema_version: stringAt(row.source_schema_version, `${path}.source_schema_version`) as string
  };
  if (row.source_updated_at !== null) fail(`${path}.source_updated_at`, 'must be null when the exchange quote update time is unknown');
  if (market.game_pk !== gamePk || market.slate_date !== gameDate || market.game_time !== gameTime) fail(path, 'must identify this exact scheduled game, date, and start');
  const populated = [market.line, market.over_ask_cents, market.under_ask_cents, market.raw_sha256, market.snapshot_id].some((entry) => entry !== null);
  if (state === 'observed_unknown_age' || populated) {
    const required = [market.game_time, market.event_ticker, market.market_ticker, market.line, market.over_ask_cents, market.under_ask_cents, market.over_ask_dollars, market.under_ask_dollars, market.over_ask_size, market.under_ask_size, market.observed_at, market.raw_sha256, market.snapshot_id, market.condition];
    if (required.some((entry) => entry === null)) fail(path, 'observed exchange evidence must include exact game identity, both asks and sizes, retrieval, condition, hash, and snapshot');
    if (!market.active) fail(path, 'observed exchange evidence must have an active, positive two-sided order');
    if (!Number.isInteger(market.line! * 2) || Number.isInteger(market.line!)) fail(`${path}.line`, 'must be a half-run threshold without a push ambiguity');
    if (!/^[a-f0-9]{64}$/.test(market.raw_sha256 ?? '') || !/^[a-f0-9]{64}$/.test(market.snapshot_id ?? '')) fail(path, 'exchange provenance must use lowercase SHA-256 digests');
    if (!market.condition!.includes(`Over ${market.line}`)) fail(`${path}.condition`, 'must identify the YES contract as the exact Over threshold');
    if (!decimalEqual(dollarsToCents(market.over_ask_dollars!), market.over_ask_cents!) || !decimalEqual(dollarsToCents(market.under_ask_dollars!), market.under_ask_cents!)) fail(path, 'display cents must preserve the native decimal asks');
  }
  return market;
}

function validateGame(value: unknown, index: number): BallparkGame {
  const path = `games[${index}]`;
  const row = objectAt(value, path);
  const gameTime = stringAt(row.game_time, `${path}.game_time`, true) as string;
  if (gameTime !== '') timestampAt(gameTime, `${path}.game_time`);
  const homeTeam = stringAt(row.home_team, `${path}.home_team`) as string;
  const awayTeam = stringAt(row.away_team, `${path}.away_team`) as string;
  if (!/^[A-Z]{2,3}$/.test(homeTeam)) fail(`${path}.home_team`, 'must be a canonical team code');
  if (!/^[A-Z]{2,3}$/.test(awayTeam)) fail(`${path}.away_team`, 'must be a canonical team code');
  const homePitcher = nullableStringAt(row.home_pitcher, `${path}.home_pitcher`);
  const awayPitcher = nullableStringAt(row.away_pitcher, `${path}.away_pitcher`);
  const gamePk = integerAt(row.game_pk, `${path}.game_pk`, 1);
  const gameDate = isoDateAt(row.game_date, `${path}.game_date`);
  return {
    game_pk: gamePk,
    game_date: gameDate,
    game_time: gameTime,
    game_status: stringAt(row.game_status, `${path}.game_status`) as string,
    game_number: integerAt(row.game_number, `${path}.game_number`, 1),
    doubleheader: stringAt(row.doubleheader, `${path}.doubleheader`) as string,
    home_team: homeTeam,
    away_team: awayTeam,
    venue: stringAt(row.venue, `${path}.venue`) as string,
    home_pitcher: homePitcher,
    away_pitcher: awayPitcher,
    weather: validateWeather(row.weather, `${path}.weather`),
    factors: validateFactors(row.factors, `${path}.factors`),
    lineup: validateLineup(row.lineup, `${path}.lineup`),
    approach_c: validateApproachC(row.approach_c, `${path}.approach_c`),
    trajectory: validateTrajectory(row.trajectory, `${path}.trajectory`),
    odds: validateOdds(row.odds, `${path}.odds`, gamePk, gameDate),
    exchange_market: validateExchangeMarket(row.exchange_market, `${path}.exchange_market`, gamePk, gameDate, gameTime)
  };
}

export function isReadyState(state: string | null | undefined): boolean {
  return ['ready', 'available', 'complete', 'confirmed', 'observed', 'verified', 'modeled', 'experimental']
    .includes((state ?? '').toLowerCase());
}

function publicationStatusAt(value: unknown, path: string): PublicationStatus {
  return enumAt(value, path, ['ready', 'degraded', 'no_slate']);
}

function validateHealth(value: unknown): PublicationHealth {
  const health = objectAt(value, 'health');
  const result = {} as PublicationHealth;
  for (const lane of ['schedule', 'weather', 'lineups', 'artifacts'] as const) {
    const record = objectAt(health[lane], `health.${lane}`);
    stringAt(record.state, `health.${lane}.state`);
    result[lane] = record;
  }
  if (health.odds !== undefined) {
    const odds = objectAt(health.odds, 'health.odds');
    enumAt(odds.state, 'health.odds.state', ['available', 'partial', 'unavailable', 'not_applicable']);
    result.odds = odds;
  } else {
    result.odds = { state: 'unavailable', source: 'Not recorded', reason: 'This legacy release predates quoted full-game totals.' };
  }
  if (health.exchange_markets !== undefined) {
    const exchange = objectAt(health.exchange_markets, 'health.exchange_markets');
    enumAt(exchange.state, 'health.exchange_markets.state', ['available', 'partial', 'unavailable']);
    result.exchange_markets = exchange;
  }
  return result;
}

function validateOddsHealth(value: unknown, games: BallparkGame[], requiresOddsLane: boolean): PublicationHealth {
  const health = validateHealth(value);
  if (!requiresOddsLane) return health;
  const odds = objectAt(objectAt(value, 'health').odds, 'health.odds');
  const state = enumAt(odds.state, 'health.odds.state', ['available', 'partial', 'unavailable', 'not_applicable']);
  const source = stringAt(odds.source, 'health.odds.source') as string;
  if (!source || odds.optional !== false) fail('health.odds', 'must identify a non-optional canonical odds lane');
  const counts = {
    current: integerAt(odds.current_games, 'health.odds.current_games', 0),
    observedUnknownAge: integerAt(odds.observed_unknown_age_games ?? 0, 'health.odds.observed_unknown_age_games', 0),
    stale: integerAt(odds.stale_games, 'health.odds.stale_games', 0),
    unavailable: integerAt(odds.unavailable_games, 'health.odds.unavailable_games', 0)
  };
  const actual = { current: 0, observed_unknown_age: 0, stale: 0, unavailable: 0 };
  for (const game of games) actual[game.odds.state] += 1;
  if (counts.current !== actual.current || counts.observedUnknownAge !== actual.observed_unknown_age || counts.stale !== actual.stale || counts.unavailable !== actual.unavailable || counts.current + counts.observedUnknownAge + counts.stale + counts.unavailable !== games.length) {
    fail('health.odds', 'counts must match every game market state exactly');
  }
  const expectedState = actual.current === games.length ? 'available' : actual.current > 0 || actual.observed_unknown_age > 0 ? 'partial' : 'unavailable';
  if (state !== expectedState) fail('health.odds.state', 'must match the summarized game market states');
  if ('acquisition_status' in odds) {
    enumAt(odds.acquisition_status, 'health.odds.acquisition_status', ['observed', 'no_quote', 'schema_error', 'transport_error']);
    nullableStringAt(odds.acquisition_error, 'health.odds.acquisition_error');
  }
  health.odds = odds;
  return health;
}

function validateExchangeHealth(value: unknown, health: PublicationHealth, games: BallparkGame[], requiresExchangeLane: boolean): PublicationHealth {
  if (!requiresExchangeLane) return health;
  const exchange = objectAt(objectAt(value, 'health').exchange_markets, 'health.exchange_markets');
  const state = enumAt(exchange.state, 'health.exchange_markets.state', ['available', 'partial', 'unavailable']);
  if (exchange.optional !== true || !(stringAt(exchange.source, 'health.exchange_markets.source') as string)) {
    fail('health.exchange_markets', 'must identify this as a non-empty supplemental optional lane');
  }
  const counts = {
    observedUnknownAge: integerAt(exchange.observed_unknown_age_games, 'health.exchange_markets.observed_unknown_age_games', 0),
    unavailable: integerAt(exchange.unavailable_games, 'health.exchange_markets.unavailable_games', 0),
  };
  const actual = { observed_unknown_age: 0, unavailable: 0 };
  for (const game of games) actual[game.exchange_market!.state] += 1;
  if (counts.observedUnknownAge !== actual.observed_unknown_age || counts.unavailable !== actual.unavailable || counts.observedUnknownAge + counts.unavailable !== games.length) {
    fail('health.exchange_markets', 'counts must match every observed-unknown-age or unavailable supplemental market');
  }
  const expectedState = actual.observed_unknown_age === games.length ? 'available' : actual.observed_unknown_age > 0 ? 'partial' : 'unavailable';
  if (state !== expectedState) fail('health.exchange_markets.state', 'must match the summarized supplemental market states');
  health.exchange_markets = exchange;
  return health;
}

function validateModel(value: unknown): JsonRecord {
  const model = objectAt(value, 'model');
  stringAt(model.name, 'model.name');
  stringAt(model.artifact_version, 'model.artifact_version');
  if (model.evidence_games !== 21_608) fail('model.evidence_games', 'must equal 21,608');
  const split = objectAt(model.split, 'model.split');
  if (split.train !== 17_075 || split.validation_2024 !== 2_302 || split.test_2025 !== 2_231) {
    fail('model.split', 'must match the published temporal evidence receipt');
  }
  const heldOut = objectAt(model.held_out_rmse, 'model.held_out_rmse');
  numberAt(heldOut.runs, 'model.held_out_rmse.runs', 0);
  numberAt(heldOut.home_runs, 'model.held_out_rmse.home_runs', 0);
  stringAt(model.statement, 'model.statement');
  const optional = objectAt(model.approach_c, 'model.approach_c');
  if (optional.state !== 'experimental_optional' || optional.used_in_headline !== false) {
    fail('model.approach_c', 'must remain experimental and outside the headline');
  }
  if (optional.batter_profiles !== 839 || optional.trajectory_entries !== 3_018_625 || optional.stadium_geometries !== 30) {
    fail('model.approach_c', 'must match the published optional-artifact inventory');
  }
  return model;
}

export function validatePayload(value: unknown): BallparkPayload {
  const root = objectAt(value, 'root');
  if (root.schema_version !== 1) fail('schema_version', 'must equal 1');
  if (root.product !== 'ballpark-weather-lab') fail('product', 'must identify ballpark-weather-lab');
  const date = isoDateAt(root.date, 'date');
  const generatedAt = timestampAt(root.generated_at, 'generated_at') as string;
  const status = publicationStatusAt(root.status, 'status');
  const rawGames = arrayAt(root.games, 'games');
  const rawIds = rawGames.map((game, index) => integerAt(objectAt(game, `games[${index}]`).game_pk, `games[${index}].game_pk`, 1));
  if (new Set(rawIds).size !== rawIds.length) fail('games', 'duplicates game ID');
  const oddsFields = rawGames.map((game, index) => 'odds' in objectAt(game, `games[${index}]`));
  const hasOdds = oddsFields.some(Boolean);
  if (hasOdds && oddsFields.some((entry) => !entry)) fail('games', 'cannot mix legacy games with quoted-market games');
  const hasOddsHealth = 'odds' in objectAt(root.health, 'health');
  if (rawGames.length > 0 && hasOdds !== hasOddsHealth) fail('health.odds', 'must accompany every quoted-market game and no legacy game');
  const exchangeFields = rawGames.map((game, index) => 'exchange_market' in objectAt(game, `games[${index}]`));
  const hasExchange = exchangeFields.some(Boolean);
  if (hasExchange && exchangeFields.some((entry) => !entry)) fail('games', 'cannot mix legacy games with supplemental exchange-market games');
  const hasExchangeHealth = 'exchange_markets' in objectAt(root.health, 'health');
  if (rawGames.length > 0 && hasExchange !== hasExchangeHealth) fail('health.exchange_markets', 'must accompany every supplemental exchange-market game and no legacy game');
  const games = rawGames.map(validateGame);
  const seen = new Set<number>();
  for (const [index, game] of games.entries()) {
    if (seen.has(game.game_pk)) fail(`games[${index}].game_pk`, `duplicates game ID ${game.game_pk}`);
    seen.add(game.game_pk);
    if (game.game_date !== date) fail(`games[${index}].game_date`, 'must match the publication date');
    if (game.weather.game_pk !== game.game_pk) fail(`games[${index}].weather.game_pk`, 'must match the game ID');
    if (game.odds.state === 'current' && (Date.parse(game.odds.source_updated_at!) > Date.parse(generatedAt) || Date.parse(game.odds.observed_at!) > Date.parse(generatedAt))) {
      fail(`games[${index}].odds`, 'current quote cannot be sourced or observed after publication generation');
    }
    if (
      game.odds.state === 'observed_unknown_age'
      && game.odds.observed_at !== null
      && Date.parse(game.odds.observed_at) > Date.parse(generatedAt) + 5 * 60_000
    ) {
      fail(`games[${index}].odds`, 'observed market evidence cannot be retrieved after publication generation');
    }
    if (
      game.exchange_market?.state === 'observed_unknown_age'
      && game.exchange_market.observed_at !== null
      && Date.parse(game.exchange_market.observed_at) > Date.parse(generatedAt) + 5 * 60_000
    ) {
      fail(
        `games[${index}].exchange_market`,
        'observed exchange evidence cannot be retrieved after publication generation'
      );
    }
  }
  if (status === 'no_slate' && games.length !== 0) fail('games', 'must be empty when status is no_slate');
  if (status !== 'no_slate' && games.length === 0) fail('games', 'must contain at least one game unless status is no_slate');
  const noSlateReason = nullableStringAt(root.no_slate_reason, 'no_slate_reason');
  if (status === 'no_slate' && !noSlateReason) fail('no_slate_reason', 'must explain a no-slate publication');
  return {
    schema_version: 1,
    product: 'ballpark-weather-lab',
    date,
    generated_at: generatedAt,
    status,
    no_slate_reason: noSlateReason,
    model: validateModel(root.model),
    health: validateExchangeHealth(root.health, validateOddsHealth(root.health, games, hasOdds), games, hasExchange),
    games
  };
}

export function validateRelease(value: unknown): ReleasePointer {
  const root = objectAt(value, 'release');
  const hash = stringAt(root.payload_sha256, 'release.payload_sha256') as string;
  if (!/^[a-f0-9]{64}$/.test(hash)) fail('release.payload_sha256', 'must be a lowercase SHA-256 digest');
  return {
    date: isoDateAt(root.date, 'release.date'),
    generated_at: timestampAt(root.generated_at, 'release.generated_at') as string,
    payload_sha256: hash
  };
}

export function validateArchiveIndex(value: unknown): ArchiveIndex {
  const root = objectAt(value, 'archive');
  const dates = arrayAt(root.dates, 'archive.dates').map((raw, index) => {
    const row = objectAt(raw, `archive.dates[${index}]`);
    const hash = stringAt(row.payload_sha256, `archive.dates[${index}].payload_sha256`) as string;
    if (!/^[a-f0-9]{64}$/.test(hash)) fail(`archive.dates[${index}].payload_sha256`, 'must be a lowercase SHA-256 digest');
    return {
      date: isoDateAt(row.date, `archive.dates[${index}].date`),
      payload_sha256: hash,
      status: publicationStatusAt(row.status, `archive.dates[${index}].status`),
      game_count: integerAt(row.game_count, `archive.dates[${index}].game_count`),
      generated_at: timestampAt(row.generated_at, `archive.dates[${index}].generated_at`) as string
    };
  });
  const seen = new Set<string>();
  for (const [index, row] of dates.entries()) {
    if (seen.has(row.date)) fail(`archive.dates[${index}].date`, `duplicates archive date ${row.date}`);
    seen.add(row.date);
  }
  return { dates };
}

export function validateGeometry(value: unknown): GeometryArtifact {
  const root = objectAt(value, 'geometry');
  const angles = arrayAt(root.angles_deg, 'geometry.angles_deg')
    .map((value, index) => numberAt(value, `geometry.angles_deg[${index}]`, -180, 180));
  const venuesRaw = objectAt(root.venues, 'geometry.venues');
  const venues: GeometryArtifact['venues'] = {};
  for (const [key, raw] of Object.entries(venuesRaw)) {
    const row = objectAt(raw, `geometry.venues.${key}`);
    const distances = arrayAt(row.wall_distance_ft, `geometry.venues.${key}.wall_distance_ft`)
      .map((value, index) => numberAt(value, `geometry.venues.${key}.wall_distance_ft[${index}]`, 100, 600));
    const heights = arrayAt(row.wall_height_ft, `geometry.venues.${key}.wall_height_ft`)
      .map((value, index) => numberAt(value, `geometry.venues.${key}.wall_height_ft[${index}]`, 0, 100));
    if (distances.length !== angles.length || heights.length !== angles.length) {
      fail(`geometry.venues.${key}`, 'must align wall arrays to angles_deg');
    }
    venues[key] = {
      ...row,
      venue_id: stringAt(row.venue_id, `geometry.venues.${key}.venue_id`) as string,
      cf_azimuth: numberAt(row.cf_azimuth, `geometry.venues.${key}.cf_azimuth`, 0, 360),
      dome_type: integerAt(row.dome_type, `geometry.venues.${key}.dome_type`, 0, 2),
      wall_distance_ft: distances,
      wall_height_ft: heights
    };
  }
  return {
    angles_deg: angles,
    venues,
    geometry_version: typeof root.geometry_version === 'string' ? root.geometry_version : undefined
  };
}
