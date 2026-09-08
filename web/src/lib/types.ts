export type PublicationStatus = 'ready' | 'degraded' | 'no_slate';

export type JsonRecord = Record<string, unknown>;

export interface HealthLane extends JsonRecord {
  state?: string;
  status?: string;
  reason?: string | null;
  detail?: string | null;
  acquisition_status?: 'observed' | 'no_quote' | 'schema_error' | 'transport_error';
  acquisition_error?: string | null;
  updated_at?: string | null;
}

export interface PublicationHealth {
  schedule: HealthLane;
  weather: HealthLane;
  lineups: HealthLane;
  odds: HealthLane;
  exchange_markets?: HealthLane;
  artifacts: HealthLane;
}

export interface GameOdds {
  game_pk: number;
  slate_date: string;
  state: 'current' | 'stale' | 'observed_unknown_age' | 'unavailable';
  reason: string | null;
  provider_event_id: string | null;
  sport: 'MLB';
  market_type: 'total';
  period: 'full_game';
  eligibility: 'pregame' | 'live' | 'final';
  sportsbook_id: string | null;
  sportsbook_name: string | null;
  provider: string;
  source_url: string | null;
  line: number | null;
  over_price: number | null;
  under_price: number | null;
  source_updated_at: string | null;
  observed_at: string | null;
  raw_sha256: string | null;
  snapshot_id: string | null;
  source_schema_version: string;
}

/**
 * A supplemental public-exchange observation. This is intentionally separate
 * from `GameOdds`: contract cents are not American sportsbook prices and do
 * not satisfy the canonical sportsbook-coverage requirement.
 */
export interface GameExchangeMarket {
  game_pk: number;
  slate_date: string;
  game_time: string | null;
  state: 'observed_unknown_age' | 'unavailable';
  reason: string | null;
  failure_reason: string | null;
  game_phase: 'pregame' | 'in_progress' | 'after_scheduled_start' | 'final' | 'unknown' | 'unavailable';
  provider: 'Kalshi';
  provider_url: string;
  event_ticker: string | null;
  market_ticker: string | null;
  market_type: 'total';
  period: 'full_game';
  quote_type: 'contract_ask';
  price_format: 'contract_cents';
  currency: 'USD';
  line: number | null;
  over_ask_dollars: string | null;
  under_ask_dollars: string | null;
  over_ask_cents: string | null;
  under_ask_cents: string | null;
  over_ask_size: string | null;
  under_ask_size: string | null;
  source_updated_at: null;
  observed_at: string | null;
  raw_sha256: string | null;
  snapshot_id: string | null;
  active: boolean;
  condition: string | null;
  source_schema_version: string;
}

export interface GameWeather {
  game_pk: number;
  state: 'verified' | 'degraded';
  source: string;
  basis: 'forecast' | 'observation' | 'indoor' | 'neutral';
  reason: string | null;
  valid_at: string | null;
  fetched_at: string | null;
  temperature_f: number;
  humidity_pct: number;
  wind_speed_mph: number;
  wind_direction_deg: number | null;
  wind_carry_mph: number;
  wind_cross_mph: number;
  air_density_index: number;
  pressure_hpa: number;
  dome_active: boolean;
  roof_state: 'open-air' | 'fixed-roof' | 'unconfirmed' | 'unknown';
}

export interface GameFactors {
  state: 'modeled' | 'held';
  reason: string | null;
  seasonal_pf_runs: number;
  seasonal_pf_hr: number;
  weather_multiplier_runs: number;
  weather_multiplier_hr: number;
  game_pf_runs: number;
  game_pf_hr: number;
  weather_delta_runs: number;
  weather_delta_hr: number;
  hr_baseline_as_of: string;
}

export interface GameLineup {
  state: 'confirmed' | 'partial' | 'not_yet_available' | 'unavailable';
  reason: string | null;
  observed_at: string | null;
  home_count: number;
  away_count: number;
}

export interface ApproachC extends JsonRecord {
  state: 'experimental' | 'not_available';
  reason: string | null;
  used_in_headline: false;
  method: string;
  home_hr_index?: number;
  away_hr_index?: number;
  home_minus_away?: number;
  home_profile_coverage?: number;
  away_profile_coverage?: number;
}

export type TrajectoryPoint = [number, number];

export interface TrajectoryArc extends JsonRecord {
  archetype: string;
  exit_velocity_mph: number;
  launch_angle_deg: number;
  spray_angle_deg: number;
  weather_distance_ft: number;
  neutral_distance_ft: number;
  carry_delta_ft: number;
  weather_points_ft: TrajectoryPoint[];
  neutral_points_ft: TrajectoryPoint[];
}

export interface GameTrajectory {
  state: 'available' | 'held';
  reason: string | null;
  integration: 'bounded Euler approximation';
  arcs: TrajectoryArc[];
}

export interface BallparkGame {
  game_pk: number;
  game_date: string;
  game_time: string;
  game_status: string;
  game_number: number;
  doubleheader: string;
  home_team: string;
  away_team: string;
  venue: string;
  home_pitcher: string | null;
  away_pitcher: string | null;
  weather: GameWeather;
  factors: GameFactors;
  lineup: GameLineup;
  approach_c: ApproachC;
  trajectory: GameTrajectory;
  odds: GameOdds;
  exchange_market?: GameExchangeMarket;
}

export interface BallparkPayload {
  schema_version: 1;
  product: 'ballpark-weather-lab';
  date: string;
  generated_at: string;
  status: PublicationStatus;
  no_slate_reason: string | null;
  model: JsonRecord;
  health: PublicationHealth;
  games: BallparkGame[];
}

export interface ReleasePointer {
  date: string;
  generated_at: string;
  payload_sha256: string;
}

export interface ArchiveEntry {
  date: string;
  payload_sha256: string;
  status: PublicationStatus;
  game_count: number;
  generated_at: string;
}

export interface ArchiveIndex {
  dates: ArchiveEntry[];
}

export interface VenueGeometry extends JsonRecord {
  venue_id: string;
  cf_azimuth: number;
  dome_type: number;
  wall_distance_ft: number[];
  wall_height_ft: number[];
}

export interface GeometryArtifact {
  angles_deg: number[];
  venues: Record<string, VenueGeometry>;
  geometry_version?: string;
}

export interface PublicationBundle {
  payload: BallparkPayload;
  release: ReleasePointer;
  archive: ArchiveIndex;
  geometry: GeometryArtifact | null;
  warnings: string[];
  payloadHash: string;
}
