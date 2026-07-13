const VERIFICATION_CODE = /(?:^|\D)\d{3}(?:[\s-]?\d{3})(?!\d)/;

export function messageForDisplay(message: string): string {
  return VERIFICATION_CODE.test(message) ? '[Verification code submitted]' : message;
}
