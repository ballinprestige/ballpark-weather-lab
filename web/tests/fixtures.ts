import type { BallparkPayload } from '../src/lib/types';

const ARTIFACT_HASH = 'a'.repeat(64);

export function readyPayload(): BallparkPayload {
  return {
    schema_version: 1,
    product: 'ballpark-weather-lab',
    date: '2026-08-27',
    generated_at: '2026-08-27T16:05:00Z',
    status: 'ready',
    no_slate_reason: null,
    model: {
      name: 'Approach B weather-adjusted park factors',
      artifact_version: '2026-04-10',
      evidence_games: 21_608,
      split: { train: 17_075, validation_2024: 2_302, test_2025: 2_231 },
      held_out_rmse: { runs: 0.5102, home_runs: 0.7173 },
      statement: 'Held-out RMSE describes this training experiment; it is not an outcome claim.',
      approach_c: {
        state: 'experimental_optional',
        used_in_headline: false,
        batter_profiles: 839,
        trajectory_entries: 3_018_625,
        stadium_geometries: 30
      }
    },
    health: {
      schedule: { state: 'available', source: 'fixture', game_count: 2 },
      weather: { state: 'available', source: 'fixture', verified_games: 2, held_games: 0 },
      lineups: { state: 'partial', source: 'fixture', confirmed_games: 1, optional: true },
      artifacts: {
        state: 'verified',
        approach_c_state: 'verified',
        optional_errors: [],
        manifest_sha256: ARTIFACT_HASH,
        files_checked: 9,
        evidence_games: 21_608,
        batter_profiles: 839,
        trajectory_entries: 3_018_625,
        stadium_geometries: 30
      }
    },
    games: [
      readyGame(1001, 'SEA', 'BOS', 'Fenway Park', '2026-08-27T21:10:00Z', 1.084, 1.121),
      {
        ...readyGame(1002, 'SD', 'SF', 'Oracle Park', '2026-08-28T01:45:00Z', 0.972, 0.944),
        lineup: {
          state: 'not_yet_available',
          reason: 'Confirmed lineups are not available yet.',
          observed_at: null,
          home_count: 0,
          away_count: 0
        },
        approach_c: {
          state: 'not_available',
          reason: 'Approach C awaits confirmed lineups.',
          used_in_headline: false,
          method: 'neutral-park double ratio'
        }
      }
    ]
  };
}

export function readyGame(
  gamePk: number,
  awayTeam: string,
  homeTeam: string,
  venue: string,
  gameTime: string,
  runsFactor: number,
  hrFactor: number
): BallparkPayload['games'][number] {
  const seasonalRuns = runsFactor > 1 ? 1.041 : 0.988;
  const seasonalHr = hrFactor > 1 ? 1.073 : 0.976;
  return {
    game_pk: gamePk,
    game_date: '2026-08-27',
    game_time: gameTime,
    game_status: 'Scheduled',
    game_number: 1,
    doubleheader: 'N',
    home_team: homeTeam,
    away_team: awayTeam,
    venue,
    home_pitcher: 'H. Starter',
    away_pitcher: 'A. Visitor',
    weather: {
      game_pk: gamePk,
      state: 'verified',
      source: 'Open-Meteo',
      basis: 'forecast',
      reason: null,
      valid_at: '2026-08-27T21:00:00Z',
      fetched_at: '2026-08-27T15:58:00Z',
      temperature_f: 82,
      humidity_pct: 58,
      wind_speed_mph: 12,
      wind_direction_deg: 225,
      wind_carry_mph: 7.2,
      wind_cross_mph: -9.6,
      air_density_index: 95.7,
      pressure_hpa: 1008.4,
      dome_active: false,
      roof_state: 'open-air'
    },
    factors: {
      state: 'modeled',
      reason: null,
      seasonal_pf_runs: seasonalRuns,
      seasonal_pf_hr: seasonalHr,
      weather_multiplier_runs: runsFactor / seasonalRuns,
      weather_multiplier_hr: hrFactor / seasonalHr,
      game_pf_runs: runsFactor,
      game_pf_hr: hrFactor,
      weather_delta_runs: runsFactor - seasonalRuns,
      weather_delta_hr: hrFactor - seasonalHr,
      hr_baseline_as_of: '2026-08-27'
    },
    lineup: {
      state: 'confirmed',
      reason: null,
      observed_at: '2026-08-27T15:55:00Z',
      home_count: 9,
      away_count: 9
    },
    approach_c: {
      state: 'experimental',
      reason: 'Optional lineup geometry is shown separately from the headline factor.',
      used_in_headline: false,
      method: 'neutral-park double ratio',
      home_hr_index: 1.031,
      away_hr_index: 0.987,
      home_minus_away: 0.044,
      home_profile_coverage: 9,
      away_profile_coverage: 9
    },
    trajectory: {
      state: 'available',
      reason: null,
      integration: 'bounded Euler approximation',
      arcs: [
        {
          archetype: 'center carry',
          launch_angle_deg: 28,
          exit_velocity_mph: 101,
          spray_angle_deg: 0,
          neutral_distance_ft: 385,
          weather_distance_ft: 399,
          neutral_points_ft: [[0, 3], [90, 62], [190, 103], [290, 88], [385, 0]],
          weather_points_ft: [[0, 3], [92, 64], [196, 108], [302, 92], [399, 0]],
          carry_delta_ft: 14
        },
        {
          archetype: 'high air',
          launch_angle_deg: 35,
          exit_velocity_mph: 97,
          spray_angle_deg: -20,
          neutral_distance_ft: 342,
          weather_distance_ft: 354,
          neutral_points_ft: [[0, 3], [80, 77], [168, 124], [260, 92], [342, 0]],
          weather_points_ft: [[0, 3], [82, 80], [174, 130], [270, 98], [354, 0]],
          carry_delta_ft: 12
        }
      ]
    }
  };
}

export function missingWeatherPayload(): BallparkPayload {
  const payload = readyPayload();
  payload.status = 'degraded';
  payload.health.weather = { state: 'partial', source: 'fixture', verified_games: 1, held_games: 1 };
  payload.games[1].weather = {
    game_pk: payload.games[1].game_pk,
    state: 'degraded',
    source: 'neutral_fallback',
    basis: 'neutral',
    reason: 'The hourly response did not include a valid game-time observation.',
    valid_at: null,
    fetched_at: '2026-08-27T16:00:00Z',
    temperature_f: 70,
    humidity_pct: 50,
    wind_speed_mph: 0,
    wind_direction_deg: null,
    wind_carry_mph: 0,
    wind_cross_mph: 0,
    air_density_index: 100,
    pressure_hpa: 1013.25,
    dome_active: false,
    roof_state: 'unknown'
  };
  payload.games[1].factors = {
    state: 'held',
    reason: 'Weather-adjusted factors require verified game-hour weather.',
    seasonal_pf_runs: 0.988,
    seasonal_pf_hr: 0.976,
    weather_multiplier_runs: 1,
    weather_multiplier_hr: 1,
    game_pf_runs: 0.988,
    game_pf_hr: 0.976,
    weather_delta_runs: 0,
    weather_delta_hr: 0,
    hr_baseline_as_of: '2026-08-27'
  };
  payload.games[1].trajectory = {
    state: 'held',
    reason: 'Trajectory context is hidden without verified game-hour weather.',
    integration: 'bounded Euler approximation',
    arcs: []
  };
  return payload;
}

export function noSlatePayload(): BallparkPayload {
  const payload = readyPayload();
  payload.status = 'no_slate';
  payload.no_slate_reason = 'The MLB schedule source reports no games for this date.';
  payload.games = [];
  payload.health.schedule = { state: 'available', source: 'fixture', game_count: 0 };
  payload.health.weather = { state: 'not_applicable', source: 'fixture', verified_games: 0, held_games: 0 };
  payload.health.lineups = { state: 'not_applicable', source: 'fixture', confirmed_games: 0, optional: true };
  return payload;
}
