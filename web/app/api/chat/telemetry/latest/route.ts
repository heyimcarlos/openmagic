const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
const upstreamPath = `${serverBase.replace(/\/$/, '')}/api/v1/chat/telemetry/latest`;

export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(upstreamPath);
  const senderPhone = requestUrl.searchParams.get('sender_phone');
  const causeId = requestUrl.searchParams.get('cause_id');
  if (senderPhone) upstreamUrl.searchParams.set('sender_phone', senderPhone);
  if (causeId) upstreamUrl.searchParams.set('cause_id', causeId);

  try {
    const response = await fetch(upstreamUrl, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    const body = await response.text();
    return new Response(body || '{}', {
      status: response.status,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to reach Python server';
    return Response.json({ error: message }, { status: 502 });
  }
}
