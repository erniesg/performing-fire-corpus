# NJP Center Video Archive shape review

The trusted VM ran the stage-one content-neutral probe from exact clean commit
`4367446b1b092020ccb95181ade3a2c93a44b944` on 2026-07-27. The endpoint-specific
governance snapshot was authorized through the full 30-second run horizon.
No laptop fallback was used.

| Fact | Observed value |
|---|---:|
| Governance snapshot SHA-256 | `3c7cdaaf301f9a066edc1361f78747bcb83c601e6c2c944cd8266eb7265d8148` |
| `robots.txt` | 200, allowed, 325 bytes |
| Video Archive page | 200, `text/html`, 53,358 bytes |
| Page response SHA-256 | `23f0f441e43a56216ea58716678f0e4db85827b43a569d70ec20445df5ab594a` |
| Categorical signature shapes | 98 |
| Nonblank text-node count | 180 |
| Embedded JSON shapes | 0 |
| HTML recovery events | 1 |
| Summary truncated | false |
| Structure SHA-256 | `e6f9a2911a325fb321202b5994b257ec50ae48bf91a60553f64e38cc33e8851b` |

The probe retained no raw HTML, prose, class or ID values, data-attribute
values, URLs, JSON keys, MIME surprises, or transport exception text. Its
sanitized shape showed exactly eight same-host file-link signatures matching
the already reviewed analogue-catalogue PDF scope. Adapter v2 therefore binds
only those eight unique `/storage/upload/…/*.pdf` URLs and bounded anchor
labels as metadata records.

Stage two must recompute this same categorical structure SHA-256 from its live
response and compare it with the value above before its metadata parser can
retain a record. Merely copying the reviewed digest into a plan or report is
not sufficient.

Linked objects remain outside this proof. The adapter issues no PDF, image,
audio, video, caption, transcript, OCR, ASR, or transformed-byte request.
