const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
const workersUrl = `${serverBase.replace(/\/$/, '')}/api/v1/demo/backpressure/workers`;

export async function POST() {
  try {
    const response = await fetch(workersUrl, {
      method: 'POST',
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
