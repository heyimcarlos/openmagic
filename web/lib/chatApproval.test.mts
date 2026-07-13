import assert from 'node:assert/strict';
import test from 'node:test';

import {
  approvalCauseFor,
  approvalSubmissionKey,
  submitChatApproval,
} from './chatApproval.ts';

test('a transport retry reuses the Cause for the exact approval submission', async () => {
  const causes = new Map<string, string>();
  const key = approvalSubmissionKey({ jobId: 'job-1', subject: 'Renewal' });
  const firstCause = approvalCauseFor(causes, key, () => 'cause-1');

  await assert.rejects(
    submitChatApproval(
      { cause_id: firstCause },
      async () => {
        throw new Error('response lost');
      },
    ),
    /could not confirm whether that approval was recorded/i,
  );

  assert.equal(approvalCauseFor(causes, key, () => 'cause-2'), firstCause);
});

test('a material approval edit receives a different Cause', () => {
  const causes = new Map<string, string>();
  const original = approvalSubmissionKey({ jobId: 'job-1', subject: 'Renewal' });
  const revised = approvalSubmissionKey({ jobId: 'job-1', subject: 'Updated renewal' });

  assert.notEqual(
    approvalCauseFor(causes, original, () => 'cause-1'),
    approvalCauseFor(causes, revised, () => 'cause-2'),
  );
});

test('a truncated successful approval response is treated as unknown', async () => {
  const response = {
    ok: true,
    json: async () => {
      throw new Error('body lost');
    },
  } as Response;

  await assert.rejects(
    submitChatApproval({}, async () => response),
    /could not confirm whether that approval was recorded/i,
  );
});
