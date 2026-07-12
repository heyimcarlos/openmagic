import assert from 'node:assert/strict';
import test from 'node:test';

import { authorizeBrowserRequest, requiresBrowserAuthentication } from './browserAuth.ts';

function basic(username: string, password: string, scheme = 'Basic'): string {
  return `${scheme} ${Buffer.from(`${username}:${password}`).toString('base64')}`;
}

test('fails closed when the browser password is not configured', async () => {
  assert.equal(await authorizeBrowserRequest(basic('openmagic', 'secret'), undefined), 'missing_configuration');
});

test('rejects absent, malformed, and incorrect credentials', async () => {
  assert.equal(await authorizeBrowserRequest(null, 'secret'), 'unauthorized');
  assert.equal(await authorizeBrowserRequest('Bearer token', 'secret'), 'unauthorized');
  assert.equal(await authorizeBrowserRequest('Basic !!!', 'secret'), 'unauthorized');
  assert.equal(await authorizeBrowserRequest(basic('someone', 'secret'), 'secret'), 'unauthorized');
  assert.equal(await authorizeBrowserRequest(basic('openmagic', 'wrong'), 'secret'), 'unauthorized');
});

test('accepts valid credentials with a case-insensitive scheme', async () => {
  assert.equal(await authorizeBrowserRequest(basic('openmagic', 'secret', 'bAsIc'), 'secret'), 'authorized');
  assert.equal(await authorizeBrowserRequest(basic('openmagic', 'sëcret'), 'sëcret'), 'authorized');
});

test('excludes only framework assets and the favicon', () => {
  assert.equal(requiresBrowserAuthentication('/_next/static/chunk.js'), false);
  assert.equal(requiresBrowserAuthentication('/_next/image'), false);
  assert.equal(requiresBrowserAuthentication('/favicon.ico'), false);
  assert.equal(requiresBrowserAuthentication('/'), true);
  assert.equal(requiresBrowserAuthentication('/api/chat'), true);
  assert.equal(requiresBrowserAuthentication('/_next/staticity'), true);
  assert.equal(requiresBrowserAuthentication('/_next/imageevil'), true);
});
