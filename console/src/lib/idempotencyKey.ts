/**
 * Generates an Idempotency-Key. Uses `crypto.randomUUID()` when available,
 * but that method only exists in secure contexts (HTTPS / localhost): the
 * console opened over plain HTTP on a LAN address has `crypto` without it,
 * and an unguarded call would throw during render/effect and unmount the
 * whole page tree (WR-C-08). The fallback builds an RFC 4122 v4 UUID from
 * `crypto.getRandomValues` (available in insecure contexts), and finally from
 * `Math.random` if no Web Crypto exists at all. The server accepts any
 * non-blank key up to 255 chars, so the value only needs to be unique and is
 * kept UUID-shaped.
 */
export function newIdempotencyKey(): string {
  const webCrypto: Crypto | undefined = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (webCrypto && typeof webCrypto.getRandomValues === "function") {
    webCrypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}
