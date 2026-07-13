const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
const historyPath = `${serverBase.replace(/\/$/, '')}/api/v1/chat/history`;

async function forward(method: 'GET' | 'DELETE', request: Request) {
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(historyPath);
  const senderPhone = requestUrl.searchParams.get('sender_phone');
  if (senderPhone) upstreamUrl.searchParams.set('sender_phone', senderPhone);
  try {
    const res = await fetch(upstreamUrl, {
      method,
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });

    const bodyText = await res.text();
    const headers = new Headers({ 'Content-Type': 'application/json; charset=utf-8' });
    return new Response(bodyText || '{}', { status: res.status, headers });
  } catch (error: any) {
    const message = error?.message || 'Failed to reach Python server';
    return new Response(JSON.stringify({ error: message }), {
      status: 502,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  }
}

export async function GET(request: Request) {
  return forward('GET', request);
}

export async function DELETE(request: Request) {
  return forward('DELETE', request);
}
