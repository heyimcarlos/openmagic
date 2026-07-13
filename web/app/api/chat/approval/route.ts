const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
const upstreamPath = `${serverBase.replace(/\/$/, '')}/api/v1/chat/approval`;

export async function POST(request: Request) {
  try {
    const body = await request.text();
    const response = await fetch(upstreamPath, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body,
      cache: 'no-store',
    });
    const responseBody = await response.text();
    return new Response(responseBody || '{}', {
      status: response.status,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to reach Python server';
    return Response.json({ error: message }, { status: 502 });
  }
}
