const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
const snapshotUrl = `${serverBase.replace(/\/$/, '')}/api/v1/demo/backpressure`;
const jobsUrl = `${snapshotUrl}/jobs`;

async function forward(response: Response): Promise<Response> {
  const body = await response.text();
  return new Response(body || '{}', {
    status: response.status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

export async function GET() {
  try {
    return forward(await fetch(snapshotUrl, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    }));
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to reach Python server';
    return Response.json({ error: message }, { status: 502 });
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  try {
    return forward(await fetch(jobsUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
    }));
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to reach Python server';
    return Response.json({ error: message }, { status: 502 });
  }
}
