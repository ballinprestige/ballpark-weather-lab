import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import type { BallparkPayload } from '../../src/lib/types';
import { missingWeatherPayload, modelAdjustmentHeldPayload, noSlatePayload, readyPayload } from '../fixtures';

function jsonText(value: unknown): string {
  return JSON.stringify(value);
}

function digest(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

function capturePath(name: string): string {
  return resolve(process.env.CAPTURE_DIR ?? resolve(process.cwd(), '../docs/screenshots'), name);
}

function alignPayloadDate(payload: BallparkPayload, date: string, generatedAt: string): void {
  const shiftMs = Date.parse(`${date}T00:00:00Z`) - Date.parse(`${payload.date}T00:00:00Z`);
  const shiftTime = (value: string | null): string | null => value === null ? null : new Date(Date.parse(value) + shiftMs).toISOString();
  payload.date = date;
  payload.generated_at = generatedAt;
  for (const game of payload.games) {
    game.game_date = date;
    game.game_time = shiftTime(game.game_time) as string;
    game.weather.valid_at = shiftTime(game.weather.valid_at);
    game.weather.fetched_at = shiftTime(game.weather.fetched_at) as string;
    game.lineup.observed_at = shiftTime(game.lineup.observed_at);
    game.odds.slate_date = date;
    game.odds.source_updated_at = shiftTime(game.odds.source_updated_at);
    game.odds.observed_at = shiftTime(game.odds.observed_at);
    if (game.exchange_market) game.exchange_market.slate_date = date;
  }
}

function reconcileHealthCounts(payload: BallparkPayload): void {
  payload.health.schedule.game_count = payload.games.length;
  const weatherHealth = payload.health.weather as { verified_games: number; held_games: number };
  weatherHealth.verified_games = payload.games.filter((game) => game.weather.state === 'verified').length;
  weatherHealth.held_games = payload.games.length - weatherHealth.verified_games;
  payload.health.lineups.confirmed_games = payload.games.filter((game) => game.lineup.state === 'confirmed').length;
  payload.health.odds.current_games = payload.games.filter((game) => game.odds.state === 'current').length;
  payload.health.odds.stale_games = payload.games.filter((game) => game.odds.state === 'stale').length;
  payload.health.odds.unavailable_games = payload.games.filter((game) => game.odds.state === 'unavailable').length;
}

function fifteenGamePayload(): BallparkPayload {
  const payload = readyPayload();
  const games: Array<[string, string, string]> = [
    ['ARI', 'COL', 'Coors Field'], ['ATL', 'MIA', 'loanDepot park'], ['BAL', 'TOR', 'Rogers Centre'],
    ['BOS', 'NYY', 'Yankee Stadium'], ['CHC', 'MIL', 'American Family Field'], ['CIN', 'STL', 'Busch Stadium'],
    ['CLE', 'DET', 'Comerica Park'], ['CWS', 'MIN', 'Target Field'], ['HOU', 'TEX', 'Globe Life Field'],
    ['KC', 'TB', 'George M. Steinbrenner Field'], ['LAA', 'OAK', 'Sutter Health Park'], ['NYM', 'PHI', 'Citizens Bank Park'],
    ['PIT', 'SD', 'Petco Park'], ['SEA', 'SF', 'Oracle Park'], ['WSH', 'LAD', 'Dodger Stadium']
  ];
  payload.games = games.map(([away, home, venue], index) => {
    const game = structuredClone(payload.games[index % payload.games.length]);
    game.game_pk = 8_200_000 + index;
    game.weather.game_pk = game.game_pk;
    game.odds.game_pk = game.game_pk;
    game.odds.provider_event_id = `covers-${game.game_pk}`;
    game.away_team = away;
    game.home_team = home;
    game.venue = venue;
    game.game_time = index === 0
      ? '2026-08-28T04:00:00Z'
      : new Date(Date.parse('2026-08-27T17:05:00Z') + (index - 1) * 24 * 60_000).toISOString();
    game.weather.wind_carry_mph = index;
    return game;
  });
  reconcileHealthCounts(payload);
  return payload;
}

async function mockPublication(
  page: Page,
  payload: BallparkPayload,
  archivePayload?: BallparkPayload,
  currentDate = payload.date,
  releaseHash?: string
): Promise<void> {
  await page.clock.setFixedTime(new Date(`${currentDate}T16:00:00Z`));
  page.on('pageerror', (error) => console.error(`Browser page error: ${error.stack ?? error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') console.error(`Browser console error: ${message.text()}`);
  });
  const currentText = jsonText(payload);
  const archive = archivePayload ?? readyPayload();
  if (!archivePayload) {
    archive.date = '2026-08-26';
    archive.generated_at = '2026-08-26T16:05:00Z';
    alignPayloadDate(archive, archive.date, archive.generated_at);
  }
  const archiveText = jsonText(archive);

  await page.route('**/data/release.json', (route) => route.fulfill({
    contentType: 'application/json',
    body: jsonText({ date: payload.date, generated_at: payload.generated_at, payload_sha256: releaseHash ?? digest(currentText) })
  }));
  await page.route('**/data/data.json', (route) => route.fulfill({ contentType: 'application/json', body: currentText }));
  await page.route('**/archive/index.json', (route) => route.fulfill({
    contentType: 'application/json',
    body: jsonText({
      dates: [
        { date: payload.date, payload_sha256: digest(currentText), status: payload.status, game_count: payload.games.length, generated_at: payload.generated_at },
        { date: archive.date, payload_sha256: digest(archiveText), status: archive.status, game_count: archive.games.length, generated_at: archive.generated_at }
      ]
    })
  }));
  await page.route(`**/archive/${archive.date}.json`, (route) => route.fulfill({ contentType: 'application/json', body: archiveText }));
  await page.route(`**/archive/${payload.date}.json`, (route) => route.fulfill({ contentType: 'application/json', body: currentText }));
}

test('desktop employer path exposes the complete evidence chain', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, readyPayload());
  await page.goto('/#slate');

  await expect(page.getByText('READY', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert', { name: 'Stale publication warning' })).toHaveCount(0);
  await expect(page.getByText(/Aug 27, 2026 · 2 games/i)).toBeVisible();
  await expect(page.locator('h1')).toHaveText('Ballpark board');
  await expect(page.getByRole('heading', { name: 'Slate wind comparison' })).toBeVisible();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByText('Weather adjustment changes this park’s normal run environment', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: /Card view|Table view/ })).toHaveCount(0);
  await expect(page.getByTestId('game-detail')).toHaveCount(0);
  await expect(page.getByText('SHA ', { exact: false }).first()).toBeVisible();
  if (process.env.CAPTURE_DEMO === '1') {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: capturePath('desktop-slate.png'), fullPage: true, animations: 'disabled' });
  }

  const themeToggle = page.getByRole('button', { name: 'Use night theme' });
  await themeToggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#0e141b');
  await page.getByRole('button', { name: 'Use day theme' }).click();
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#f5f7fa');

  await page.getByRole('link', { name: /Open San Diego Padres.*San Francisco Giants.*details/i }).click();
  await expect(page).toHaveURL(/#game\/1002$/);
  await expect(page.getByTestId('game-detail').getByRole('heading', { name: /San Diego Padres at San Francisco Giants/i })).toBeVisible();
  await expect(page.getByText('Park wind diagram', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Park-context breakdown' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Flight-path comparison' })).toBeVisible();
  await expect(page.getByText('Approach C awaits confirmed lineups.')).toBeVisible();
  if (process.env.CAPTURE_DEMO === '1') {
    await page.getByRole('button', { name: 'Use night theme' }).click();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: capturePath('game-night.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: 'Use day theme' }).click();
  }

  await page.getByRole('link', { name: 'Data Health' }).click();
  await expect(page.getByRole('link', { name: 'Data Health' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'Publication lanes' })).toBeVisible();
  await expect(page.getByText('Payload SHA-256')).toBeVisible();
  await expect(page.getByText('Publication state').locator('..')).toContainText('Ready');
  await expect(page.getByText('Freshness').locator('..')).toContainText('Current');

  await page.getByRole('link', { name: 'Method', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'The five-step critical path' })).toBeVisible();
  await expect(page.getByText('21,608')).toBeVisible();

  await page.getByRole('link', { name: 'History' }).click();
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible();
  await page.getByRole('button', { name: /Open Wed, Aug 26, 2026 snapshot/i }).click();
  await expect(page.getByText('Historical snapshot')).toBeVisible();
  await page.getByRole('link', { name: 'Data Health' }).click();
  await expect(page.getByText('Freshness').locator('..')).toContainText('Historical snapshot');
});

test('quoted full-game totals show both actual prices and stale or missing markets are explicit', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  payload.games[1].odds = {
    ...payload.games[1].odds,
    state: 'stale', reason: 'selected-book quote is 1200 seconds old; freshness limit is 900 seconds', line: 9,
    over_price: 105, under_price: -125
  };
  payload.health.odds = { state: 'partial', source: 'fixture Covers total table', current_games: 1, stale_games: 1, unavailable_games: 0, optional: false };
  await mockPublication(page, payload);
  await page.goto('/#slate');
  const seaAtBos = page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1001"]') });
  const sdAtSf = page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1002"]') });
  await expect(seaAtBos).toContainText('O -105 · U -115');
  await expect(sdAtSf).toContainText('bet365 · stale');
  await page.getByRole('link', { name: /Open San Diego Padres.*San Francisco Giants.*details/i }).click();
  await expect(page.getByRole('heading', { name: 'Total, Over & Under' })).toBeVisible();
  await expect(page.getByText('Stale quote:', { exact: false })).toContainText('not current');
  await expect(page.getByText('Source updated', { exact: false })).toBeVisible();
});

test('mobile slate and game details remain compact and touch safe', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'));
  await mockPublication(page, readyPayload());
  await page.goto('/#slate');

  const second = page.getByRole('link', { name: /Open San Diego Padres.*San Francisco Giants.*details/i });
  await expect(page.locator('h1')).toHaveText('Ballpark board');
  await expect(page.getByTestId('game-detail')).toHaveCount(0);
  const [gridBox, windBox] = await Promise.all([
    page.locator('.game-grid').boundingBox(),
    page.locator('.wind-field').boundingBox()
  ]);
  expect(gridBox).not.toBeNull();
  expect(windBox).not.toBeNull();
  expect((windBox?.y ?? 0) - ((gridBox?.y ?? 0) + (gridBox?.height ?? 0))).toBeLessThan(300);
  if (process.env.CAPTURE_DEMO === '1') {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: capturePath('mobile-slate.png'), fullPage: true, animations: 'disabled' });
  }
  await second.scrollIntoViewIfNeeded();
  const slateScrollY = await page.evaluate(() => window.scrollY);
  await second.click();
  await expect(page).toHaveURL(/#game\/1002$/);
  const stationHeading = page.getByRole('heading', { name: /San Diego Padres at San Francisco Giants/i });
  await expect(stationHeading).toBeFocused();
  await expect(page.getByRole('link', { name: 'Back to ballpark board' })).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await expect(stationHeading).toBeVisible();
  await expect(page.getByText('Park wind diagram', { exact: true })).toBeVisible();

  const tooSmall = await page.locator('button:visible, .game-card h3 a:visible, .station-back:visible').evaluateAll((elements) => elements
    .map((element) => ({ label: element.textContent?.trim(), box: element.getBoundingClientRect() }))
    .filter(({ box }) => box.width < 44 || box.height < 44)
    .map(({ label, box }) => ({ label, width: box.width, height: box.height })));
  expect(tooSmall).toEqual([]);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.getByRole('link', { name: 'Back to ballpark board' }).click();
  await expect(page).toHaveURL(/#slate$/);
  await expect(page.locator('h1')).toHaveText('Ballpark board');
  expect(Math.abs(await page.evaluate(() => window.scrollY) - slateScrollY)).toBeLessThanOrEqual(2);
  await expect(second).toBeFocused();
});

test('single-game mobile wind strip keeps its game in view', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'));
  const payload = readyPayload();
  payload.games = payload.games.slice(0, 1);
  reconcileHealthCounts(payload);
  await mockPublication(page, payload);
  await page.goto('/#slate');

  const scroller = page.locator('.wind-field__scroller');
  await expect(page.getByRole('heading', { name: 'Slate wind comparison' })).toBeVisible();
  await expect(page.getByText(/Aug 27, 2026 · 1 game/i)).toBeVisible();
  const dimensions = await scroller.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth
  }));
  expect(dimensions.scrollWidth - dimensions.clientWidth).toBeLessThanOrEqual(1);
});

test('field instrument keeps a real wind vector, roof hold, and missing direction distinct', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  await mockPublication(page, payload);
  await page.goto('/#game/1001');
  await expect(page.locator('.wind-stream')).toHaveCount(4);
  await expect(page.locator('.wind-decomposition')).toContainText('FROM');
  await expect(page.locator('.wind-decomposition')).toContainText('CROSS');
  await expect(page.getByRole('heading', { name: 'Total, Over & Under' })).toBeVisible();
  const [fieldBox, viewport] = await Promise.all([
    page.locator('.park-wind svg').boundingBox(),
    page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }))
  ]);
  expect(fieldBox).not.toBeNull();
  expect((fieldBox?.x ?? -1) >= 0).toBe(true);
  expect((fieldBox?.y ?? -1) >= 0).toBe(true);
  expect((fieldBox?.x ?? Number.POSITIVE_INFINITY) + (fieldBox?.width ?? Number.POSITIVE_INFINITY)).toBeLessThanOrEqual(viewport.width);
  expect((fieldBox?.y ?? Number.POSITIVE_INFINITY) + (fieldBox?.height ?? Number.POSITIVE_INFINITY)).toBeLessThanOrEqual(viewport.height);
  const samples = page.getByRole('tab', { name: /center carry|high air/i });
  await expect(samples).toHaveCount(2);
  await samples.first().focus();
  await page.keyboard.press('ArrowRight');
  await expect(samples.nth(1)).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Home');
  await expect(samples.first()).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('End');
  await expect(samples.nth(1)).toHaveAttribute('aria-selected', 'true');
  await samples.first().click();
  const [neutralPath, weatherPath] = await Promise.all([
    page.locator('.trajectory-neutral').getAttribute('d'),
    page.locator('.trajectory-weather').getAttribute('d')
  ]);
  const finalX = (path: string | null) => Number((path ?? '').trim().split(/[ L]/).filter(Boolean).at(-1)?.split(',')[0]);
  expect(finalX(weatherPath)).toBeGreaterThan(finalX(neutralPath));

  payload.games[0].weather.roof_state = 'fixed-roof';
  payload.games[0].weather.dome_active = true;
  await mockPublication(page, payload);
  await page.reload();
  await expect(page.getByText('Roof active · outdoor wind withheld')).toBeVisible();
  await expect(page.locator('.wind-stream')).toHaveCount(0);

  payload.games[0].weather.roof_state = 'open-air';
  payload.games[0].weather.dome_active = false;
  payload.games[0].weather.wind_direction_deg = null;
  await mockPublication(page, payload);
  await page.reload();
  await expect(page.getByText('Direction not reported · no vector shown')).toBeVisible();
  await expect(page.getByText('Calm · 0 mph')).toHaveCount(0);
});

test('319px game detail retains the field and avoids horizontal overflow', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'));
  await page.setViewportSize({ width: 319, height: 480 });
  await mockPublication(page, readyPayload());
  await page.goto('/#game/1002');
  await expect(page.getByText('Park wind diagram', { exact: true })).toBeVisible();
  await expect(page.locator('.park-wind')).toContainText('CARRY');
  await expect(page.getByRole('heading', { name: 'Total, Over & Under' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
});

test('a full fifteen-game slate stays a compact, sortable table', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, fifteenGamePayload());
  await page.goto('/#slate');

  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('row')).toHaveCount(16);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

  const firstMatchup = page.locator('.ledger tbody tr').first().getByRole('link');
  await expect(firstMatchup).toHaveAccessibleName(/Atlanta Braves.*Miami Marlins/i);
  await page.locator('.sort-control select').selectOption('time');
  await expect(firstMatchup).toHaveAccessibleName(/Atlanta Braves.*Miami Marlins/i);
  await page.locator('.sort-control select').selectOption('wind');
  await expect(firstMatchup).toHaveAccessibleName(/Washington Nationals.*Los Angeles Dodgers/i);
  await page.locator('.sort-control select').selectOption('venue');
  await expect(firstMatchup).toHaveAccessibleName(/Chicago Cubs.*Milwaukee Brewers/i);
  await page.locator('.sort-control select').selectOption('movement');
  await expect(firstMatchup).toHaveAccessibleName(/Arizona Diamondbacks.*Colorado Rockies/i);
});

test('an older current release is labeled stale and cannot present as ready', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  alignPayloadDate(payload, '2020-08-27', '2020-08-27T16:05:00Z');
  const archive = readyPayload();
  alignPayloadDate(archive, '2020-08-26', '2020-08-26T16:05:00Z');
  await mockPublication(page, payload, archive, '2020-08-28');
  await page.goto('/#slate');

  const warning = page.getByRole('alert', { name: 'Stale publication warning' });
  await expect(warning).toBeVisible();
  await expect(warning.getByText('STALE', { exact: true })).toBeVisible();
  await expect(warning).toContainText('Showing Thu, Aug 27, 2020');
  await expect(warning).toContainText('America/New_York');
  await expect(warning).toContainText('Do not treat this slate as current.');
  await expect(warning.getByText('READY', { exact: true })).toHaveCount(0);

  await page.getByRole('link', { name: 'Data Health' }).click();
  await expect(page.getByText('Publication state').locator('..')).toContainText('Ready');
  await expect(page.getByText('Freshness').locator('..')).toContainText('Stale');
});

test('no-slate is a valid publication state', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, noSlatePayload());
  await page.goto('/#slate');
  await expect(page.getByRole('heading', { name: 'No games scheduled' })).toBeVisible();
  await expect(page.getByText(/valid publication, not a loading error/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review Data Health' })).toBeVisible();
});

test('a missing-weather game is held without hiding its valid neighbor', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, missingWeatherPayload());
  await page.goto('/#slate');
  await expect(page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1001"]') })).toContainText('runs');
  await expect(page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1002"]') })).toContainText('Weather held');
  for (const option of ['time', 'wind', 'venue', 'movement']) {
    await page.locator('.sort-control select').selectOption(option);
    await expect(page.locator('.ledger tbody tr').last().getByRole('link')).toHaveAccessibleName(/San Diego Padres.*San Francisco Giants/i);
  }
  await page.getByRole('button', { name: 'Open air' }).click();
  await expect(page.getByRole('link', { name: /Open San Diego Padres.*San Francisco Giants.*details/i })).toHaveCount(0);
  await page.getByRole('button', { name: 'Incomplete' }).click();
  await expect(page.locator('.ledger tbody tr')).toHaveCount(1);
  await expect(page.locator('.ledger tbody tr').getByRole('link')).toHaveAccessibleName(/San Diego Padres.*San Francisco Giants/i);
  await page.getByRole('button', { name: 'All' }).click();
  const heldRow = page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1002"]') });
  await expect(heldRow).not.toContainText('0.988');
  await expect(heldRow).not.toContainText('0.976');
  await expect(heldRow).not.toContainText('70°');
  await expect(heldRow).not.toContainText('100.0');
  await page.getByRole('link', { name: /Open San Diego Padres.*San Francisco Giants.*details/i }).click();
  await expect(page.getByTestId('weather-hold')).toContainText('Weather-adjusted headline withheld');
  await expect(page.getByText(/hourly response did not include/i).first()).toBeVisible();
  await expect(page.locator('.conditions-strip')).toContainText('Withheld');
  await expect(page.locator('.conditions-strip')).not.toContainText('70°F');
});

test('malformed artifacts fail closed with a useful retry state', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  (payload.games[0].factors as unknown as Record<string, unknown>).game_pf_runs = null;
  await mockPublication(page, payload);
  await page.goto('/#slate');
  await expect(page.getByRole('heading', { name: 'Release verification failed' })).toBeVisible();
  await expect(page.getByText(/game_pf_runs must be a finite number/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Check the release again' })).toBeVisible();
});

test('a release hash mismatch fails closed before slate values render', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  await mockPublication(page, payload, undefined, payload.date, 'b'.repeat(64));
  await page.goto('/#slate');
  await expect(page.getByRole('heading', { name: 'Release verification failed' })).toBeVisible();
  await expect(page.getByText(/publication hash mismatch/i)).toBeVisible();
  await expect(page.getByText('Ballpark board', { exact: true })).toHaveCount(0);
  await expect(page.getByText('RUNS PF 1.084')).toHaveCount(0);
});

test('duplicate game IDs fail closed', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  payload.games[1].game_pk = payload.games[0].game_pk;
  await mockPublication(page, payload);
  await page.goto('/#slate');
  await expect(page.getByRole('heading', { name: 'Release verification failed' })).toBeVisible();
  await expect(page.getByText(/duplicates game ID/i)).toBeVisible();
});

test('a held learned adjustment retains verified LAD wind and total evidence', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, modelAdjustmentHeldPayload());
  await page.goto('/#slate');

  const row = page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1002"]') });
  await expect(row).toContainText('Out · +7.2 carry');
  await expect(row).toContainText('Model adjustment held');
  await expect(row).not.toContainText('Weather held');
  await expect(row).toContainText('8.5');

  await page.getByRole('link', { name: /Open San Diego Padres.*Los Angeles Dodgers.*details/i }).click();
  await expect(page.getByTestId('weather-hold')).toContainText('Model validation pending');
  await expect(page.getByTestId('weather-hold')).toContainText(/0° wind axis/i);
  await expect(page.locator('.factor-headline')).toHaveCount(0);
  await expect(page.locator('.conditions-strip')).toContainText('82°F');
  await expect(page.locator('.conditions-strip')).toContainText('12 mph from SW');
  const decomposition = page.locator('.decomposition');
  await expect(decomposition).toContainText('Seasonal park baselines:');
  await expect(decomposition).toContainText('Runs 0.970');
  await expect(decomposition).toContainText('Home runs 1.070');
  await expect(decomposition).toContainText('Weather multipliers and game factors remain withheld.');
  await expect(decomposition.getByRole('table')).toHaveCount(0);
  await expect(page.getByText('Kalshi contract asks')).toHaveCount(0);
});

const FENWAY_GEOMETRY = {
  angles_deg: [-45, 0, 45],
  venues: {
    BOS: { venue_id: 'fenway_park', cf_azimuth: 35, dome_type: 0, wall_distance_ft: [310, 420, 380], wall_height_ft: [8, 10, 8] }
  }
};

function retainedExchangePayload(): BallparkPayload {
  const payload = readyPayload();
  for (const game of payload.games) {
    (game as unknown as Record<string, unknown>).exchange_market = {
      state: 'observed_unknown_age', reason: 'Quote update time is not published.', failure_reason: 'Kalshi public market-data request returned 503.',
      provider: 'Kalshi', provider_url: 'https://external-api.kalshi.com/trade-api/v2', event_ticker: `KXMLBTOTAL-${game.game_pk}`, market_ticker: `KXMLBTOTAL-${game.game_pk}-9`,
      slate_date: payload.date, game_pk: game.game_pk, game_time: game.game_time, market_type: 'total', period: 'full_game', game_phase: 'pregame', quote_type: 'contract_ask',
      condition: 'Over 8.5 runs scored', price_format: 'contract_cents', currency: 'USD', line: 8.5, over_ask_dollars: '0.4900', under_ask_dollars: '0.5200',
      over_ask_cents: '49.0000', under_ask_cents: '52.0000', over_ask_size: '12', under_ask_size: '8', source_updated_at: null,
      observed_at: '2026-08-27T16:00:00Z', raw_sha256: 'c'.repeat(64), snapshot_id: 'd'.repeat(64), active: true, source_schema_version: 'kalshi-public-total-v1'
    };
  }
  (payload.health as unknown as Record<string, unknown>).exchange_markets = {
    state: 'available', source: 'Kalshi public market-data API', optional: true, observed_unknown_age_games: 2, unavailable_games: 0
  };
  return payload;
}

test('cold game hydrates geometry without waiting for a delayed archive', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, readyPayload());
  await page.route('**/park_geometry.json', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 80));
    await route.fulfill({ contentType: 'application/json', body: jsonText(FENWAY_GEOMETRY) });
  });
  let releaseArchive: (() => void) | undefined;
  const archiveHeld = new Promise<void>((resolve) => { releaseArchive = resolve; });
  await page.route('**/archive/index.json', async (route) => {
    await archiveHeld;
    await route.fulfill({ status: 503, body: 'archive unavailable' });
  });
  await page.goto('/#game/1001');
  await expect(page.getByTestId('game-detail')).toBeVisible();
  await expect(page.locator('.field-wall')).toBeVisible();
  releaseArchive?.();
});

test('optional updates are fenced across archive and Return Live generations', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, readyPayload());
  let releaseOldGeometry: (() => void) | undefined;
  const oldGeometryHeld = new Promise<void>((resolve) => { releaseOldGeometry = resolve; });
  let geometryRequest = 0;
  await page.route('**/park_geometry.json', async (route) => {
    geometryRequest += 1;
    if (geometryRequest === 1) {
      await oldGeometryHeld;
      await route.fulfill({ contentType: 'application/json', body: jsonText(FENWAY_GEOMETRY) });
      return;
    }
    await route.fulfill({ status: 503, body: 'newer geometry outage' });
  });
  await page.goto('/#slate');
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('link', { name: 'History' }).click();
  await page.getByRole('button', { name: /Open Wed, Aug 26, 2026 snapshot/i }).click();
  await expect(page.getByText('Historical snapshot')).toBeVisible();
  await page.getByRole('link', { name: 'History' }).click();
  await page.getByRole('button', { name: 'Return to current release' }).click();
  await expect.poll(() => geometryRequest).toBeGreaterThanOrEqual(2);
  await page.getByRole('link', { name: 'Slate', exact: true }).click();
  await page.getByRole('link', { name: /Open Seattle Mariners at Boston Red Sox details/i }).click();
  await expect(page.locator('.field-wall')).toHaveCount(0);
  await page.getByRole('link', { name: 'Data Health' }).click();
  const warnings = page.locator('section.warning-ledger').filter({ has: page.getByRole('heading', { name: 'Degraded enhancements' }) });
  await expect(warnings).toContainText('Park geometry unavailable');
  // The superseded held request never retries. The current generation gets
  // exactly its bounded two attempts before reporting the optional failure.
  await expect.poll(() => geometryRequest).toBe(3);
  releaseOldGeometry?.();
  await page.waitForTimeout(25);
  await expect(warnings).toContainText('Park geometry unavailable');
});

test('stale archive and geometry callbacks cannot overwrite a visibility refresh', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, readyPayload());
  const archived = readyPayload();
  alignPayloadDate(archived, '2026-08-26', '2026-08-26T16:05:00Z');
  const archivedText = jsonText(archived);
  const oldArchiveIndex = {
    dates: [{
      date: archived.date,
      payload_sha256: digest(archivedText),
      status: archived.status,
      game_count: archived.games.length,
      generated_at: archived.generated_at
    }]
  };
  let releaseOldArchive: (() => void) | undefined;
  let releaseOldGeometry: (() => void) | undefined;
  const oldArchiveHeld = new Promise<void>((resolve) => { releaseOldArchive = resolve; });
  const oldGeometryHeld = new Promise<void>((resolve) => { releaseOldGeometry = resolve; });
  let archiveRequests = 0;
  let geometryRequests = 0;
  await page.route('**/archive/index.json', async (route) => {
    archiveRequests += 1;
    if (archiveRequests === 1) {
      await oldArchiveHeld;
      await route.fulfill({ contentType: 'application/json', body: jsonText(oldArchiveIndex) });
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: jsonText({ dates: [] }) });
  });
  await page.route('**/park_geometry.json', async (route) => {
    geometryRequests += 1;
    if (geometryRequests === 1) {
      await oldGeometryHeld;
      await route.fulfill({ contentType: 'application/json', body: jsonText(FENWAY_GEOMETRY) });
      return;
    }
    await route.fulfill({ status: 503, body: 'newer geometry outage' });
  });
  await page.goto('/#slate');
  await expect(page.getByRole('table')).toBeVisible();
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => archiveRequests).toBe(2);
  await expect.poll(() => geometryRequests).toBeGreaterThanOrEqual(2);
  await page.getByRole('link', { name: 'Data Health' }).click();
  const warnings = page.locator('section.warning-ledger').filter({ has: page.getByRole('heading', { name: 'Degraded enhancements' }) });
  await expect(warnings).toContainText('Park geometry unavailable');
  // The superseded request is cancelled; the current visibility refresh alone
  // gets the bounded two geometry attempts.
  await expect.poll(() => geometryRequests).toBe(3);
  await expect(page.getByText('Geometry artifact').locator('..')).toContainText('Unavailable');
  releaseOldArchive?.();
  releaseOldGeometry?.();
  await page.waitForTimeout(25);
  await expect(warnings).toContainText('Park geometry unavailable');
  await page.getByRole('link', { name: 'History' }).click();
  await expect(page.getByRole('heading', { name: 'History is not available yet' })).toBeVisible();
});

test('sequential optional failures retain both warning notices', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, readyPayload());
  await page.route('**/archive/index.json', (route) => route.fulfill({ status: 503, body: 'archive outage' }));
  let releaseGeometry: (() => void) | undefined;
  const geometryHeld = new Promise<void>((resolve) => { releaseGeometry = resolve; });
  await page.route('**/park_geometry.json', async (route) => {
    await geometryHeld;
    await route.fulfill({ status: 503, body: 'geometry outage' });
  });
  await page.goto('/#slate');
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('link', { name: 'Data Health' }).click();
  const warnings = page.locator('section.warning-ledger').filter({ has: page.getByRole('heading', { name: 'Degraded enhancements' }) });
  await expect(warnings).toContainText('History unavailable');
  await expect(warnings).not.toContainText('Park geometry unavailable');
  releaseGeometry?.();
  await expect(warnings).toContainText('Park geometry unavailable');
});

test('retained Kalshi asks show the provider failure without becoming current', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = retainedExchangePayload();
  await mockPublication(page, payload);
  await page.goto('/#slate');
  const marketRow = page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1001"]') });
  await expect(marketRow).toContainText('Over 49¢ · Under 52¢');
  await expect(marketRow).toContainText('Update failed: Kalshi public market-data request returned 503.');
  await expect(marketRow).toContainText('observed Aug 27, 9:00 AM PDT');
  await expect(page.getByText(/current sportsbook totals/i)).toHaveCount(0);
  await marketRow.getByRole('link').click();
  const exchangeDetail = page.locator('section.exchange-market');
  await expect(exchangeDetail).toContainText('YES ask49¢');
  await expect(exchangeDetail).toContainText('NO ask52¢');
  await expect(exchangeDetail).toContainText('Captured/as of Aug 27, 9:00 AM PDT');
  await expect(exchangeDetail).toContainText('Update failed. Retaining the captured asks above');
  await page.getByRole('link', { name: 'Data Health' }).click();
  const failureLedger = page.locator('section.warning-ledger').filter({ has: page.getByRole('heading', { name: 'Kalshi update failed' }) });
  await expect(failureLedger).toContainText('Kalshi public market-data request returned 503.');
});

test('retained pregame exchange evidence becomes after-scheduled-start at the controlled clock', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = retainedExchangePayload();
  payload.games[0].game_time = '2026-08-27T15:00:00Z';
  payload.games[0].exchange_market!.game_time = payload.games[0].game_time;
  await mockPublication(page, payload);
  await page.goto('/#slate');
  await expect(page.locator('.slate-meta')).toContainText('2 observed exchange totals · 1 upcoming');
  const elapsed = page.locator('.ledger tbody tr').filter({ has: page.locator('[data-game-key="1001"]') });
  await expect(elapsed).toContainText('after scheduled start');
  await elapsed.getByRole('link').click();
  await expect(page.getByTestId('game-detail').locator('.detail-status-line')).toContainText('Status at capture: Scheduled');
  await expect(page.locator('.exchange-market')).toContainText('Market phaseafter scheduled start');
  await expect(page.locator('.exchange-market')).toContainText('source age unknown');
});

test('319px retained Kalshi row preserves capture time and update failure', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'));
  await page.setViewportSize({ width: 319, height: 480 });
  await mockPublication(page, retainedExchangePayload());
  await page.goto('/#slate');
  const row = page.locator('.compact-game-row').first();
  await expect(row).toContainText('Over 49¢ · Under 52¢');
  await expect(row).toContainText('captured 9:00 AM PDT');
  await expect(row).toContainText('Update failed: Kalshi public market-data request returned 503.');
  await row.getByRole('link').click();
  const exchangeDetail = page.locator('section.exchange-market');
  await expect(exchangeDetail).toContainText('Captured/as of Aug 27, 9:00 AM PDT');
  await expect(exchangeDetail).toContainText('Update failed. Retaining the captured asks above');
  await page.getByRole('link', { name: 'Data Health' }).click();
  const failureLedger = page.locator('section.warning-ledger').filter({ has: page.getByRole('heading', { name: 'Kalshi update failed' }) });
  await expect(failureLedger).toContainText('Kalshi public market-data request returned 503.');
});
