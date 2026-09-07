import { describe, expect, it } from 'vitest';
import { findVenueGeometry } from './geometry';
import type { GeometryArtifact } from './types';

const geometry: GeometryArtifact = {
  angles_deg: [-45, 0, 45],
  venues: {
    ARI: { venue_id: 'chase_field', cf_azimuth: 270, dome_type: 1, wall_distance_ft: [330, 407, 334], wall_height_ft: [8, 25, 8] }
  }
};

describe('findVenueGeometry', () => {
  it('requires the audited venue identity instead of inferring it from the home club', () => {
    expect(findVenueGeometry(geometry, 'Chase Field')?.venue_id).toBe('chase_field');
    expect(findVenueGeometry(geometry, 'Estadio Alfredo Harp Helu')).toBeNull();
  });
});
