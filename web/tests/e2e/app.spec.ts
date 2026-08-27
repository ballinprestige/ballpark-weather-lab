import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import type { BallparkPayload } from '../../src/lib/types';
import { missingWeatherPayload, noSlatePayload, readyPayload } from '../fixtures';

function jsonText(value: unknown): string {
  return JSON.stringify(value);
}

function digest(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

async function mockPublication(page: Page, payload: BallparkPayload, archivePayload?: BallparkPayload): Promise<void> {
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
    body: jsonText({ date: payload.date, generated_at: payload.generated_at, payload_sha256: digest(currentText) })
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

  await expect(page.getByRole('heading', { name: /Aug 27, 2026 slate/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /Seattle Mariners.*Boston Red Sox/i })).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('heading', { name: 'Park wind diagram' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Decomposition ladder' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Trajectory theater' })).toBeVisible();
  await expect(page.getByText('SHA ', { exact: false }).first()).toBeVisible();

  await page.getByRole('button', { name: /San Diego Padres.*San Francisco Giants/i }).click();
  await expect(page.getByTestId('game-detail').getByRole('heading', { name: /San Diego Padres at San Francisco Giants/i })).toBeVisible();
  await expect(page.getByText('Approach C awaits confirmed lineups.')).toBeVisible();

  await page.getByRole('link', { name: 'Data Health' }).click();
  await expect(page.getByRole('link', { name: 'Data Health' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'Four publication lanes' })).toBeVisible();
  await expect(page.getByText('Payload SHA-256')).toBeVisible();

  await page.getByRole('link', { name: 'Method' }).click();
  await expect(page.getByRole('heading', { name: 'The five-step critical path' })).toBeVisible();
  await expect(page.getByText('21,608')).toBeVisible();

  await page.getByRole('link', { name: 'History' }).click();
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible();
  await page.getByRole('button', { name: /Open Wed, Aug 26, 2026 snapshot/i }).click();
  await expect(page.getByText('Historical snapshot')).toBeVisible();
});

test('mobile slate remains compact, expandable, and touch safe', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'));
  await mockPublication(page, readyPayload());
  await page.goto('/#slate');

  const first = page.getByRole('button', { name: /Seattle Mariners.*Boston Red Sox/i });
  const second = page.getByRole('button', { name: /San Diego Padres.*San Francisco Giants/i });
  await expect(first).toHaveAttribute('aria-expanded', 'true');
  await second.click();
  await expect(first).toHaveAttribute('aria-expanded', 'false');
  await expect(second).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('heading', { name: 'Park wind diagram' })).toBeVisible();

  const tooSmall = await page.locator('button:visible, nav a:visible').evaluateAll((elements) => elements
    .map((element) => ({ label: element.textContent?.trim(), box: element.getBoundingClientRect() }))
    .filter(({ box }) => box.width < 44 || box.height < 44)
    .map(({ label, box }) => ({ label, width: box.width, height: box.height })));
  expect(tooSmall).toEqual([]);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test('no-slate is a valid publication state', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, noSlatePayload());
  await page.goto('/#slate');
  await expect(page.getByRole('heading', { name: 'No games on the slate' })).toBeVisible();
  await expect(page.getByText(/valid publication, not a loading error/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review Data Health' })).toBeVisible();
});

test('a missing-weather game is held without hiding its valid neighbor', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  await mockPublication(page, missingWeatherPayload());
  await page.goto('/#slate');
  await expect(page.getByText('RUNS PF 1.084')).toBeVisible();
  await expect(page.getByText('WEATHER HOLD')).toBeVisible();
  await page.getByRole('button', { name: /San Diego Padres.*San Francisco Giants/i }).click();
  await expect(page.getByTestId('weather-hold')).toContainText('Weather-adjusted headline withheld');
  await expect(page.getByText(/hourly response did not include/i).first()).toBeVisible();
});

test('malformed artifacts fail closed with a useful retry state', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  (payload.games[0].factors as unknown as Record<string, unknown>).game_pf_runs = null;
  await mockPublication(page, payload);
  await page.goto('/#slate');
  await expect(page.getByRole('heading', { name: 'The field sheet did not pass readback' })).toBeVisible();
  await expect(page.getByText(/game_pf_runs must be a finite number/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Check the release again' })).toBeVisible();
});

test('duplicate game IDs fail closed', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'));
  const payload = readyPayload();
  payload.games[1].game_pk = payload.games[0].game_pk;
  await mockPublication(page, payload);
  await page.goto('/#slate');
  await expect(page.getByRole('heading', { name: 'The field sheet did not pass readback' })).toBeVisible();
  await expect(page.getByText(/duplicates game ID/i)).toBeVisible();
});
