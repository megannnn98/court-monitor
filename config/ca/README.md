# TLS trust anchors for fedsfm.ru

`fedsfm.ru` presents a certificate issued by the Russian Ministry of Digital
Development CA, which is in neither the system trust store nor `certifi`.
Without this bundle the fetch fails with `CERTIFICATE_VERIFY_FAILED`.

The bundle is passed to `httpx` **only for fedsfm.ru** (see
`sources/fedsfm_live.py`). It is deliberately *not* installed into the system
trust store: that would let this CA vouch for any hostname the project
fetches, including the Telegram channels and courts we read over the normal
public CAs.

## Contents

| Certificate | SHA-256 fingerprint | Valid until |
|---|---|---|
| `Russian Trusted Root CA` | `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31` | 2032-02-27 |
| `Russian Trusted Sub CA` (`subca_ssl_rsa2024`) | `77:3D:D9:39:…` (SKI, matches the leaf's AKI) | 2029-07-19 |

Bundle file SHA-256: `4ab21fc3f37793557c69279432f5def448acbd8aaf578e1112a40aa5773cbd89`

## Provenance

- Root: `https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt`
- Intermediate: `http://nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt`, the
  `CA Issuers` URI published in the leaf certificate's Authority Information
  Access extension.

Note that the server sends **only** its leaf certificate, so the intermediate
has to come from here — and that the older `russian_trusted_sub_ca_pem.crt`
published on gu-st.ru is a *different* Sub CA (2022, SKI `D1:E1:71:0D:…`) that
does not sign the current fedsfm.ru leaf. Chains built with it fail.

## Residual risk — read before trusting this

Both certificates were first retrieved over a connection that could not yet be
verified (that is the bootstrap problem this file exists to solve). Pinning
them therefore buys **continuity, not authenticity**: from now on a change in
the chain is detectable, but the very first fetch cannot be proven not to have
been intercepted.

If that matters for your threat model, verify the root fingerprint above
against an independent source before relying on imported data. The stakes are
real — records from this list are matched against named individuals, so a
substituted file means false accusations.

## Rotation

The leaf expires 2027-07-09 and the intermediate 2029-07-19. When either
rotates, re-read the leaf's AIA extension to find the current intermediate:

```sh
openssl s_client -connect fedsfm.ru:443 -servername fedsfm.ru </dev/null 2>/dev/null \
  | openssl x509 -noout -ext authorityKeyIdentifier,authorityInfoAccess
```
