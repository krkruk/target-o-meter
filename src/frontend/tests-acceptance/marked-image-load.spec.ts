// S-03 Phase 8 acceptance: the marked image actually LOADS in a real browser.
//
// The prior scoring-flow.spec asserted `toBeVisible()` on the marked-image
// <img> — but that only checks the element is rendered, NOT that the image
// bytes fetched successfully. A broken <img> with a bad src (the original
// ISSUE 2 bug: an internal `minio:9000` host the browser can't resolve, or a
// presigned S3 URL exposing AWSAccessKeyId/Signature) is still "visible" as a
// DOM element. This spec closes that gap:
//
//   1. the marked image's `naturalWidth > 0` (the bytes actually decoded — the
//      real "image is there" proof the user asked for);
//   2. the <img src> is the BFF proxy path `/v1/scoring/jobs/{id}/marked-image`
//      (no `minio`, no `AWSAccessKeyId`, no `Signature` — the leak/host bugs
//      the proxy route fixes); and
//   3. no image request failed during the flow.
//
// Drives the same stack global-setup boots (runserver + Vite + qcluster,
// VISION_DETECTOR=mock). Backend-agnostic: USE_S3=False here, but the BFF
// proxy path is the same under either backend, so the src-shape assertion
// guards the prod/MinIO posture too.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..', '..');
const FIXTURE_IMG = resolve(REPO_ROOT, 'src', 'domains', 'vision', 'tests', 'fixtures', '12.jpg');

test('marked image loads via the BFF proxy (no minio host, no AWS creds leak)', async ({ page }) => {
  // Collect failed image requests so the assertion can name them (a 404 on the
  // proxy route, or a DNS failure on an internal minio host, would land here).
  const failedImageRequests: string[] = [];
  page.on('response', (response) => {
    const url = response.url();
    const ct = response.headers()['content-type'] || '';
    if (ct.startsWith('image/') && response.status() >= 400) {
      failedImageRequests.push(`${response.status()} ${url}`);
    }
  });
  // Also capture requests that *targeted* the internal host / S3 creds leak
  // (before the fix the SPA emitted these; after, none should occur).
  const leakyRequests: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (url.includes('minio:') || url.includes('AWSAccessKeyId') || url.includes('Signature=')) {
      leakyRequests.push(url);
    }
  });

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto('/dashboard');
  await page.getByRole('button', { name: /add photos/i }).click();
  await page.getByRole('combobox', { name: /caliber/i }).selectOption('9x19mm');
  await page.getByRole('combobox', { name: /distance/i }).selectOption('25');
  await page.getByRole('button', { name: /next/i }).click();

  const fileInput = page.getByLabel(/select a photo of your target/i);
  await fileInput.setInputFiles({
    name: 'target.jpg',
    mimeType: 'image/jpeg',
    buffer: readFileSync(FIXTURE_IMG),
  });

  // Wait for the qcluster worker to drive the job to /results.
  await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });

  const markedImg = page.getByRole('img', { name: /marked target/i });
  await expect(markedImg).toBeVisible();

  // ISSUE 2 regression guards — the proxy route is the fix:
  //   1. the src is the BFF path (no internal minio host, no S3 creds).
  const src = await markedImg.getAttribute('src');
  expect(src, 'marked-image src must be the BFF proxy path').toMatch(
    /\/v1\/scoring\/jobs\/[0-9a-f-]+\/marked-image$/,
  );
  expect(src, 'src must not leak the internal minio host').not.toContain('minio');
  expect(src, 'src must not leak AWSAccessKeyId').not.toContain('AWSAccessKeyId');
  expect(src, 'src must not leak a Signature').not.toContain('Signature');

  //   2. the image actually LOADED (bytes decoded) — the real "image is there"
  //      proof. naturalWidth===0 means a broken image (bad host / fetch failed).
  await expect.poll(
    async () => await markedImg.evaluate((el) => (el as HTMLImageElement).naturalWidth),
    { message: 'marked image naturalWidth should be > 0 (bytes decoded)', timeout: 10_000 },
  ).toBeGreaterThan(0);

  //   3. no image request failed, and no leaky request occurred.
  expect(failedImageRequests, 'no failed image requests during the flow').toEqual([]);
  expect(leakyRequests, 'no minio-host / AWS-creds image requests').toEqual([]);
});
