import assert from 'node:assert/strict';
import test from 'node:test';

import { revisionReadyMessage } from '../components/workflows/CockpitConversation.tsx';

test('reapproval copy follows the current revision', () => {
  assert.equal(revisionReadyMessage(3), 'Revision 3 is ready.');
});
