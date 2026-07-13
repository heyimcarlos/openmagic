import { isRecord } from './typeGuards';

export type ChatApprovalPayload = Record<string, unknown>;
export type ChatApprovalFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const UNKNOWN_APPROVAL_MESSAGE = (
  'I could not confirm whether that approval was recorded. I refreshed the conversation. '
  + 'If the email is still awaiting approval, try again.'
);

export function approvalSubmissionKey(submission: Record<string, unknown>): string {
  return JSON.stringify(submission);
}

export function approvalCauseFor(
  causes: Map<string, string>,
  submissionKey: string,
  createId: () => string = () => crypto.randomUUID(),
): string {
  const existing = causes.get(submissionKey);
  if (existing) return existing;

  const cause = `ui-approval:${createId()}`;
  causes.set(submissionKey, cause);
  return cause;
}

export async function submitChatApproval(
  body: ChatApprovalPayload,
  request: ChatApprovalFetch = fetch,
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await request('/api/chat/approval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(UNKNOWN_APPROVAL_MESSAGE);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(UNKNOWN_APPROVAL_MESSAGE);
  }
  const parsed = isRecord(payload) ? payload : {};
  if (!response.ok) {
    const detail = typeof parsed.detail === 'string'
      ? parsed.detail
      : typeof parsed.error === 'string'
        ? parsed.error
        : 'I could not record that approval. Please review the latest email and try again.';
    throw new Error(detail);
  }
  return parsed;
}
