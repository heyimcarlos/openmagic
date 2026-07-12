export type BrowserAuthorization = 'authorized' | 'missing_configuration' | 'unauthorized';

const USERNAME = 'openmagic';

async function digest(value: string): Promise<Uint8Array> {
  const bytes = new TextEncoder().encode(value);
  return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
}

async function equalSecret(actual: string, expected: string): Promise<boolean> {
  const [actualDigest, expectedDigest] = await Promise.all([digest(actual), digest(expected)]);
  let difference = 0;
  for (let index = 0; index < actualDigest.length; index += 1) {
    difference |= actualDigest[index] ^ expectedDigest[index];
  }
  return difference === 0;
}

export async function authorizeBrowserRequest(
  authorization: string | null,
  expectedPassword: string | undefined,
): Promise<BrowserAuthorization> {
  if (!expectedPassword) return 'missing_configuration';

  const encoded = authorization?.match(/^Basic\s+(.+)$/i)?.[1];
  if (!encoded) return 'unauthorized';

  try {
    const binary = atob(encoded);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const credentials = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    const separator = credentials.indexOf(':');
    if (separator < 0) return 'unauthorized';

    const username = credentials.slice(0, separator);
    const password = credentials.slice(separator + 1);
    const [usernameMatches, passwordMatches] = await Promise.all([
      equalSecret(username, USERNAME),
      equalSecret(password, expectedPassword),
    ]);
    return usernameMatches && passwordMatches ? 'authorized' : 'unauthorized';
  } catch {
    return 'unauthorized';
  }
}

export function requiresBrowserAuthentication(pathname: string): boolean {
  return !(
    pathname === '/favicon.ico' ||
    pathname.startsWith('/_next/static/') ||
    pathname === '/_next/image'
  );
}
