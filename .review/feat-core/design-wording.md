# Proposed design wording updates (docs/designs/linkx.md)

The design is Approved, so these need the author's OK. Each makes the document describe the code as built; none changes behaviour that the design promised.

| # | Section | Now says | Proposed | Why |
|---|---|---|---|---|
| 1 | Producers, exception mapping | `httpx.InvalidURL` to `invalid_url` | Drop it: URLs are validated before any request, and an unparseable redirect `Location` reaches linkx as `httpx.RemoteProtocolError`, which maps to `connect` and is retried | The `InvalidURL` path cannot be reached during a send |
| 2 | Producers, body | "The decoded size is counted as the body streams" | "linkx asks only for gzip or deflate and decompresses them itself in bounded steps, so a body never grows past `--max-bytes`; a declared `Content-Length` over the cap fails before reading; any other content encoding is `unexpected`" | Fix for the gzip-bomb finding (737 MB peak, now 10.9 MB) |
| 3 | Producers, body | "decoded with the response charset, or UTF-8" | add: "or UTF-8 when the charset is unknown or not a text encoding" | Fix for base64/rot13 charsets failing the fetch |
| 4 | Producers, address check | "public by `ipaddress.is_global`" | "public by `ipaddress.is_global`, not multicast, not in 192.0.0.0/24 or 192.88.99.0/24; an IPv6 address that embeds an IPv4 one (mapped, NAT64, IPv4-compatible) is judged by that IPv4 address" | Fix for the NAT64 and special-range finding |
| 5 | Record and outcomes | "`final_url` and `http_status` are `null` when no response arrived" | "... when the failure happened before a response to the final request arrived (a hop refused by the address check also has none)" | Matches how hook failures behave |
| 6 | Completion and interrupt | "restores the previous handler before it returns" | "restores the previous handler after the sink is closed, so a late Ctrl-C cannot cut the summary short"; and: "a second Ctrl-C records the job whose parse is running as `cancelled`; that parse thread still finishes before the process exits" | Fix for the lost-summary finding; states the second-Ctrl-C detail |
| 7 | Failure handling | "exits 1 without writing further records" | add: "the sink refuses every write after one has failed" | Fix for writes after an output failure |
| 8 | Record and outcomes | (nothing) | "U+2028, U+2029 and U+0085 are escaped in output lines" | Fix for the line-separator finding |
