export type PublicationStatus = 'ready' | 'degraded' | 'no_slate';

export type JsonRecord = Record<string, unknown>;

export interface HealthLane extends JsonRecord {
  state?: string;
  status?: string;
  reason?: string | null;
  detail?: string | null;
  updated_at?: string | null;
}

export interface PublicationHealth {
  schedule: HealthLane;
  weather: HealthLane;
  lineups: HealthLane;
  artifacts: HealthLane;
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
