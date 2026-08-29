import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import type { BallparkPayload } from '../../src/lib/types';
import { missingWeatherPayload, noSlatePayload, readyPayload } from '../fixtures';

function jsonText(value: unknown): string {
  return JSON.stringify(value);
}

function digest(text: string): string {
  return createHash('sha256').update(text).digest('hex');
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
    game.away_team = away;
    game.home_team = home;
    game.venue = venue;
    game.game_time = index === 0
      ? '2026-08-28T04:00:00Z'
      : new Date(Date.parse('2026-08-27T17:05:00Z') + (index - 1) * 24 * 60_000).toISOString();
    game.weather.wind_carry_mph = index;
    return game;
  });
  payload.health.schedule.game_count = 15;
  payload.health.weather.verified_games = 15;
  payload.health.lineups.confirmed_games = 8;
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
    archive.games.forEach((game) => game.game_date = archive.date);
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
  await expect(page.locator('h1')).toHaveText('Daily park factors');
  await expect(page.getByRole('heading', { name: 'Slate wind comparison' })).toBeVisible();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Card view' })).toBeVisible();
  const [countsBox, synopsisBox] = await Promise.all([
    page.locator('.slate-counts').boundingBox(),
    page.locator('.slate-synopsis').boundingBox()
  ]);
  expect(countsBox).not.toBeNull();
  expect(synopsisBox).not.toBeNull();
  expect((countsBox?.x ?? 0) + (countsBox?.width ?? 0)).toBeLessThanOrEqual(synopsisBox?.x ?? 0);
  await expect(page.getByTestId('game-detail')).toHaveCount(0);
  await expect(page.getByText('SHA ', { exact: false }).first()).toBeVisible();
  if (process.env.CAPTURE_DEMO === '1') {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: resolve(process.cwd(), '../docs/screenshots/desktop-slate.png'), fullPage: true, animations: 'disabled' });
  }

  const themeToggle = page.getByRole('button', { name: 'Use night theme' });
  await themeToggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#0e141b');
  await page.getByRole('button', { name: 'Use day theme' }).click();
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#f5f7fa');

  await page.getByRole('button', { name: /Open San Diego Padres.*San Francisco Giants.*details/i }).click();
  await expect(page).toHaveURL(/#game\/1002$/);
  await expect(page.getByTestId('game-detail').getByRole('heading', { name: /San Diego Padres at San Francisco Giants/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Park wind diagram' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Decomposition ladder' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Trajectory theater' })).toBeVisible();
  await expect(page.getByText('Approach C awaits confirmed lineups.')).toBeVisible();
  if (process.env.CAPTURE_DEMO === '1') {
    await page.getByRole('button', { name: 'Use night theme' }).click();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: resolve(process.cwd(), '../docs/screenshots/game-night.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: 'Use day theme' }).click();
  }

  await page.getByRole('link', { name: 'Data Health' }).click();
  await expect(page.getByRole('link', { name: 'Data Health' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'Four publication lanes' })).toBeVisible();
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

test('mobile slate and game details remain compact and touch safe', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'));
  await mockPublication(page, readyPayload());
  await page.goto('/#slate');

  const second = page.getByRole('link', { name: /Open San Diego Padres.*San Francisco Giants.*details/i });
  await expect(page.locator('h1')).toHaveText('Daily park factors');
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
    await page.screenshot({ path: resolve(process.cwd(), '../docs/screenshots/mobile-slate.png'), fullPage: true, animations: 'disabled' });
  }
  await second.scrollIntoViewIfNeeded();
  const slateScrollY = await page.evaluate(() => window.scrollY);
  await second.click();
  await expect(page).toHaveURL(/#game\/1002$/);
  const stationHeading = page.getByRole('heading', { name: /San Diego Padres at San Francisco Giants/i });
  await expect(stationHeading).toBeFocused();
  await expect(page.getByRole('link', { name: 'Back to daily park factors' })).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await expect(stationHeading).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Park wind diagram' })).toBeVisible();

  const tooSmall = await page.locator('button:visible, nav a:visible, .game-card h3 a:visible, .station-back:visible').evaluateAll((elements) => elements
    .map((element) => ({ label: element.textContent?.trim(), box: element.getBoundingClientRect() }))
    .filter(({ box }) => box.width < 44 || box.height < 44)
    .map(({ label, box }) => ({ label, width: box.width, height: box.height })));
  expect(tooSmall).toEqual([]);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.getByRole('link', { name: 'Back to daily park factors' }).click();
  await expect(page).toHaveURL(/#slate$/);
  await expect(page.locator('h1')).toHaveText('Daily park factors');
  expect(Math.abs(await page.evaluate(() => window.scrollY) - slateScrollY)).toBeLessThanOrEqual(2);
  await expect(second).toBeFocused();
});

test('single-game mobile wind strip keeps its game in view', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'));
  const payload = readyPayload();
  payload.games = payload.games.slice(0, 1);
  await mockPublication(page, payload);
  await page.goto('/#slate');

  const scroller = page.locator('.wind-field__scroller');
  await expect(page.getByRole('heading', { name: 'Slate wind comparison' })).toBeVisible();
  const firstPitchText = await page.locator('.slate-synopsis strong').first().textContent();
  expect(firstPitchText?.split('·')[0]).not.toContain('–');
  const dimensions = await scroller.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth
  }));
  expect(dimensions.scrollWidth - dimensions.clientWidth).toBeLessThanOrEqual(1);
});

test('a full fifteen-game slate defaults to the table and retains the responsive card path', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, fifteenGamePayload());
  await page.goto('/#slate');

  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('row')).toHaveCount(16);
  await page.getByRole('button', { name: 'Card view' }).click();
  const cards = page.locator('.game-card');
  await expect(cards).toHaveCount(15);
  const boxes = await cards.evaluateAll((elements) => elements.slice(0, 4).map((element) => element.getBoundingClientRect().toJSON()));
  expect(boxes[0].y).toBe(boxes[1].y);
  expect(boxes[1].y).toBe(boxes[2].y);
  expect(boxes[3].y).toBeGreaterThan(boxes[0].y);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

  const firstCard = cards.first().getByRole('link');
  await expect(firstCard).toHaveAccessibleName(/Arizona Diamondbacks.*Colorado Rockies/i);
  await page.locator('.sort-control select').selectOption('time');
  await expect(firstCard).toHaveAccessibleName(/Atlanta Braves.*Miami Marlins/i);
  await page.locator('.sort-control select').selectOption('wind');
  await expect(firstCard).toHaveAccessibleName(/Washington Nationals.*Los Angeles Dodgers/i);
  await page.locator('.sort-control select').selectOption('venue');
  await expect(firstCard).toHaveAccessibleName(/Chicago Cubs.*Milwaukee Brewers/i);
  await page.locator('.sort-control select').selectOption('movement');
  await expect(firstCard).toHaveAccessibleName(/Arizona Diamondbacks.*Colorado Rockies/i);

  await page.getByRole('button', { name: 'Table view' }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('row')).toHaveCount(16);
  await page.getByRole('button', { name: 'Card view' }).click();
  await expect(cards).toHaveCount(15);
});

test('an older current release is labeled stale and cannot present as ready', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  payload.date = '2020-08-27';
  payload.generated_at = '2020-08-27T16:05:00Z';
  payload.games.forEach((game) => game.game_date = payload.date);
  const archive = readyPayload();
  archive.date = '2020-08-26';
  archive.generated_at = '2020-08-26T16:05:00Z';
  archive.games.forEach((game) => game.game_date = archive.date);
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
  await page.getByRole('button', { name: 'Card view' }).click();
  await expect(page.getByText('RUNS PF 1.084')).toBeVisible();
  await expect(page.getByText('Weather hold')).toBeVisible();
  for (const option of ['time', 'wind', 'venue', 'movement']) {
    await page.locator('.sort-control select').selectOption(option);
    await expect(page.locator('.game-card').last().getByRole('link')).toHaveAccessibleName(/San Diego Padres.*San Francisco Giants/i);
  }
  await page.getByRole('button', { name: 'Open air' }).click();
  await expect(page.getByRole('link', { name: /Open San Diego Padres.*San Francisco Giants.*details/i })).toHaveCount(0);
  await page.getByRole('button', { name: 'Incomplete' }).click();
  await expect(page.locator('.game-card')).toHaveCount(1);
  await expect(page.locator('.game-card').getByRole('link')).toHaveAccessibleName(/San Diego Padres.*San Francisco Giants/i);
  await page.getByRole('button', { name: 'All' }).click();
  await page.getByRole('button', { name: 'Table view' }).click();
  const heldRow = page.getByRole('row', { name: /SD at SF/i });
  await expect(heldRow).not.toContainText('0.988');
  await expect(heldRow).not.toContainText('0.976');
  await expect(heldRow).not.toContainText('70°');
  await expect(heldRow).not.toContainText('100.0');
  await page.getByRole('button', { name: 'Card view' }).click();
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
  await expect(page.getByText('Daily park factors', { exact: true })).toHaveCount(0);
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
