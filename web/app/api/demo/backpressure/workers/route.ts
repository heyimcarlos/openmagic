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

export async function DELETE(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  const workerId = body && typeof body === 'object' && 'worker_id' in body
    ? body.worker_id
    : undefined;
  if (typeof workerId !== 'string' || !workerId) {
    return Response.json({ error: 'worker_id is required' }, { status: 400 });
  }
  try {
    const response = await fetch(`${workersUrl}/${encodeURIComponent(workerId)}`, {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
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
