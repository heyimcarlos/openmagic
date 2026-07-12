import { NextRequest, NextResponse } from 'next/server';

import { authorizeBrowserRequest, requiresBrowserAuthentication } from './lib/browserAuth';

function unauthorized(): NextResponse {
  return new NextResponse('Authentication required', {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="OpenMagic", charset="UTF-8"' },
  });
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  if (!requiresBrowserAuthentication(request.nextUrl.pathname)) return NextResponse.next();

  const authorization = await authorizeBrowserRequest(
    request.headers.get('authorization'),
    process.env.OPENMAGIC_BROWSER_PASSWORD,
  );
  if (authorization === 'missing_configuration') {
    return new NextResponse('Workflow interaction credential is not configured', { status: 503 });
  }
  return authorization === 'authorized' ? NextResponse.next() : unauthorized();
}

export const config = {
  matcher: ['/:path*'],
};
