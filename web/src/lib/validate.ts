import type {
  ApproachC,
  ArchiveIndex,
  BallparkGame,
  BallparkPayload,
  GameFactors,
  GameLineup,
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
  return {
    game_pk: integerAt(row.game_pk, `${path}.game_pk`, 1),
    game_date: isoDateAt(row.game_date, `${path}.game_date`),
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
    trajectory: validateTrajectory(row.trajectory, `${path}.trajectory`)
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
  return result;
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
  const status = publicationStatusAt(root.status, 'status');
  const games = arrayAt(root.games, 'games').map(validateGame);
  const seen = new Set<number>();
  for (const [index, game] of games.entries()) {
    if (seen.has(game.game_pk)) fail(`games[${index}].game_pk`, `duplicates game ID ${game.game_pk}`);
    seen.add(game.game_pk);
    if (game.game_date !== date) fail(`games[${index}].game_date`, 'must match the publication date');
    if (game.weather.game_pk !== game.game_pk) fail(`games[${index}].weather.game_pk`, 'must match the game ID');
  }
  if (status === 'no_slate' && games.length !== 0) fail('games', 'must be empty when status is no_slate');
  if (status !== 'no_slate' && games.length === 0) fail('games', 'must contain at least one game unless status is no_slate');
  const noSlateReason = nullableStringAt(root.no_slate_reason, 'no_slate_reason');
  if (status === 'no_slate' && !noSlateReason) fail('no_slate_reason', 'must explain a no-slate publication');
  return {
    schema_version: 1,
    product: 'ballpark-weather-lab',
    date,
    generated_at: timestampAt(root.generated_at, 'generated_at') as string,
    status,
    no_slate_reason: noSlateReason,
    model: validateModel(root.model),
    health: validateHealth(root.health),
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
