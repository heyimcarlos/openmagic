import assert from 'node:assert/strict';
import test from 'node:test';

import { messageForDisplay } from './chatDisplay.ts';

test('verification codes are redacted before optimistic display', () => {
  assert.equal(messageForDisplay('482913'), '[Verification code submitted]');
  assert.equal(messageForDisplay('my code is 482913'), '[Verification code submitted]');
  assert.equal(messageForDisplay('my code is 482-913'), '[Verification code submitted]');
  assert.equal(messageForDisplay('my code is 482 913'), '[Verification code submitted]');
});

test('numbers that are not six-digit tokens remain visible', () => {
  assert.equal(messageForDisplay('policy 4829137'), 'policy 4829137');
});
