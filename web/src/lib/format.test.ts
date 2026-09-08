import { describe, expect, it } from 'vitest';
import { isGameHeld, isModelAdjustmentHeld, isWeatherHeld } from './format';
import { modelAdjustmentHeldPayload } from '../../tests/fixtures';

describe('factor holds with verified weather', () => {
  it('keeps physical weather distinct from an unavailable learned adjustment', () => {
    const game = modelAdjustmentHeldPayload().games[1];

    expect(isWeatherHeld(game)).toBe(false);
    expect(isModelAdjustmentHeld(game)).toBe(true);
    expect(isGameHeld(game)).toBe(true);
    expect(game.weather.wind_carry_mph).toBe(7.2);
    expect(game.odds.line).toBe(8.5);
  });
});
