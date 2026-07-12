import { NextRequest, NextResponse } from 'next/server';

const USERNAME = 'openmagic';

function unauthorized(): NextResponse {
  return new NextResponse('Authentication required', {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="OpenMagic", charset="UTF-8"' },
  });
}

export function middleware(request: NextRequest): NextResponse {
  const expectedPassword = process.env.OPENMAGIC_WORKFLOW_INTERACTION_TOKEN;
  if (!expectedPassword) {
    return new NextResponse('Workflow interaction credential is not configured', { status: 503 });
  }

  const authorization = request.headers.get('authorization');
  if (!authorization?.startsWith('Basic ')) return unauthorized();

  try {
    const credentials = atob(authorization.slice('Basic '.length));
    const separator = credentials.indexOf(':');
    const username = credentials.slice(0, separator);
    const password = credentials.slice(separator + 1);
    if (separator < 0 || username !== USERNAME || password !== expectedPassword) {
      return unauthorized();
    }
  } catch {
    return unauthorized();
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
