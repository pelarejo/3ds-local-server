# NB binary format

NB is the compact binary representation used by the 3LS server's
3HS-compatible catalog API. The server implementation is in
[`threels_server/nb.py`](../threels_server/nb.py); the matching client-side
container parsers and object definitions live in
[`nblib`](../../3hs-frontend/include/nblib/).

## HTTP context

NB responses use `Content-Type: application/octet-stream`. Both successful NB
responses and binary error responses include `x-minimum`, whose value comes
from `NB_MINIMUM_VERSION` and defaults to `1.5.11`. The header communicates the
minimum compatible client version; it is not part of the NB body.

The implemented endpoint mapping is:

| Endpoint | Success body | Notes |
| --- | --- | --- |
| `GET /nbapi/title-index` | `TIDX` | Catalog hierarchy and aggregates |
| `GET /nbapi/title/category/{category}/{subcategory}` | `NBAR` of partial titles | One shared string blob |
| `GET /nbapi/title/random` | `TITL` | One full title; not cacheable |
| `GET /nbapi/title/{id}` | `TITL` | One full title |
| `GET /nbcontent/{id}/request` | `TOKN` | Short-lived download grant |
| `GET /nbcontent/{id}?token=...` | Raw CIA bytes | Not NB; supports byte ranges |

Failures from these routes are represented as `RSLT`. See the current route
behavior in the [catalog views](../apps/catalog/views.py) and
[content views](../apps/content/views.py).

## Encoding rules

- All integers are unsigned and little-endian.
- All headers and blob entries are aligned to four bytes.
- Magic values are exactly four ASCII bytes.
- Strings are UTF-8 followed by a NUL byte.
- A blob starts with four zero bytes. Offset `0` therefore means absent; the
  first stored value begins at offset `4`.
- A pointer stored in an object header is an offset into that object's blob,
  not an absolute response offset.
- Padding bytes carry no data and are currently emitted as zero.

The backend rejects embedded NUL bytes in strings. Empty scalar strings use
offset `0`. Alternative-name arrays reject empty entries and encode an empty
array as offset `0`.

## Containers

### Single object

`RSLT`, `TIDX`, `TITL`, and `TOKN` use the 16-byte single-object envelope.

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | Object magic |
| 4 | 4 | Object-envelope size, currently `16` |
| 8 | 4 | Object-header size |
| 12 | 4 | Blob size |
| 16 | variable | Object header |
| `16 + header_size` | variable | Object blob |

The total encoded size is `16 + header_size + blob_size`. The client parser is
implemented in
[`single_object.hh`](../../3hs-frontend/include/nblib/nb/single_object.hh).

### Object array (`NBAR`)

Partial-title lists and both levels of index hierarchy use an object array.

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | `NBAR` |
| 4 | 4 | Array-envelope size, currently `20` |
| 8 | 4 | Element count |
| 12 | 4 | Size of each element header |
| 16 | 4 | Shared-blob size |
| 20 | `count * element_size` | Consecutive element headers |
| `20 + count * element_size` | variable | Shared blob |

Pointers in every element are relative to this array's shared blob. The client
implementation is
[`array.hh`](../../3hs-frontend/include/nblib/nb/array.hh).

### UTF-8 raw array (`NBRA`)

Alternative names are stored as a raw array embedded in a parent blob.

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | `NBRA` |
| 4 | 4 | Raw-array header size, currently `16` |
| 8 | 4 | String count |
| 12 | 4 | Payload size |
| 16 | 4 | Zero sentinel |
| 20 | variable | Consecutive NUL-terminated UTF-8 strings, then padding |

The parent stores an offset to the `NBRA` magic. There is no per-string offset
table: the client walks exactly `count` terminated strings after the sentinel.
See
[`raw_array.hh`](../../3hs-frontend/include/nblib/nb/raw_array.hh).

## Payload layouts

Offsets in the following tables are relative to the payload's object or element
header, not to the HTTP body.

### Result (`RSLT`)

The object header is 8 bytes.

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u32` | Composed result code |
| 4 | `u32` | Message string offset |

The result code is `(namespace << 16) | reason`. The server currently defines:

| Namespace | Value | Reason | Value |
| --- | ---: | --- | ---: |
| Title | 1 | Success | 0 |
| Category | 2 | Unauthorized | 1 |
| Subcategory | 3 | Not found | 2 |
| Token | 4 | Invalid argument | 3 |
| User | 6 | Exception occurred | 5 |
| Index | 7 | Invalid operation | 7 |
| Internal | 12 |  |  |

For example, Title/Not found is `(1 << 16) | 2`, or `0x00010002`.
The client definition is
[`result.hh`](../../3hs-frontend/include/nblib/objects/result.hh). The client
knows additional namespace and reason values that this server does not
currently emit.

### Index (`TIDX`)

The `TIDX` object header is 48 bytes.

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u32` | Title count |
| 4 | `u32` | Official-title count |
| 8 | `u32` | Legit-title count |
| 12 | 4 bytes | Padding |
| 16 | `u64` | Sum of artifact sizes |
| 24 | `u64` | Sum of download counts |
| 32 | `u32` | Category-array offset |
| 36 | 4 bytes | Padding |
| 40 | `u64` | Generation time as a Unix timestamp |

In this server implementation, `title count` and `official-title count` are
both the number of eligible titles. `legit-title count` is the number whose
`content_type` equals `1`. Only listed titles with an enabled artifact
participate in these aggregates.

`category-array offset` points into the `TIDX` blob to an `NBAR` with 56-byte
category headers:

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | 32 bytes | Index aggregate block, as above |
| 32 | `u32` | Category protocol ID |
| 36 | `u32` | Display-name offset |
| 40 | `u32` | Slug/name offset |
| 44 | `u32` | Description offset |
| 48 | `u8` | Priority |
| 49 | 3 bytes | Padding |
| 52 | `u32` | Subcategory-array offset |

Each subcategory array is an `NBAR` inside the category array's shared blob.
Its element headers are 48 bytes: the same 32-byte aggregate block followed by
`u32` protocol ID, display-name offset, slug/name offset, and description
offset. Those four string offsets are relative to the nested subcategory
array's own shared blob. The subcategory-array pointer in a category header is
instead relative to the category array's shared blob.

This change of offset base at each nested `NBAR` is important. The corresponding
client structures are in
[`index.hh`](../../3hs-frontend/include/nblib/objects/index.hh).

### Partial title array

Category listings return a top-level `NBAR` with 64-byte element headers.

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u64` | Nintendo title ID |
| 8 | `u64` | Artifact size |
| 16 | `u64` | Flags |
| 24 | `u64` | Download count |
| 32 | `u32` | Database title ID |
| 36 | `u32` | Name offset |
| 40 | `u32` | Legacy alternative-name offset |
| 44 | `u32` | Product-code offset |
| 48 | `u16` | Version |
| 50 | `u8` | Content type |
| 51 | `u8` | Category protocol ID |
| 52 | `u8` | Subcategory protocol ID |
| 53 | 3 bytes | Padding |
| 56 | `u32` | Alternative-names `NBRA` offset |
| 60 | `u32` | Preferred alternative-name index |

All offsets use the partial-title array's shared blob. The client type is
[`simple_title.hh`](../../3hs-frontend/include/nblib/objects/simple_title.hh).

### Full title (`TITL`)

The full-title object header is 144 bytes.

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | 16 bytes | Seed, zero-filled when absent |
| 16 | `u64` | Artifact size |
| 24 | `u64` | Nintendo title ID |
| 32 | `u64` | Added Unix timestamp |
| 40 | `u64` | Updated Unix timestamp |
| 48 | `u64` | Download count |
| 56 | `u64` | Flags |
| 64 | `u32` | Database title ID |
| 68 | `u32` | Name offset |
| 72 | `u32` | Legacy alternative-name offset |
| 76 | `u32` | Region offset |
| 80 | `u32` | Filename offset |
| 84 | `u32` | Description offset |
| 88 | `u32` | Product-code offset |
| 92 | `u16` | Version |
| 94 | `u8` | Content type |
| 95 | `u8` | Category protocol ID |
| 96 | `u8` | Subcategory protocol ID |
| 97 | `u8` | Listed flag (`0` or `1`) |
| 98 | 2 bytes | Padding |
| 100 | `u32` | Alternative-names `NBRA` offset |
| 104 | `u32` | Preferred alternative-name index |
| 108 | 32 bytes | File checksum, zero-filled when absent |
| 140 | 4 bytes | Trailing padding |

Seed and checksum values must be absent or exactly 16 and 32 bytes,
respectively. String and `NBRA` offsets are relative to the `TITL` blob. See
[`title.hh`](../../3hs-frontend/include/nblib/objects/title.hh).

As a worked offset example, the database ID is at byte `16 + 64 = 80` in the
complete HTTP body. The blob begins at byte `16 + 144 = 160`; if the name field
at body byte `84` contains `4`, the UTF-8 name begins at body byte `164`.

### Download token (`TOKN`)

The object header is 16 bytes.

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u64` | Expiry as a Unix timestamp |
| 8 | `u32` | Database title ID |
| 12 | `u32` | Download-token string offset |

The token offset is relative to the `TOKN` blob, which begins at body byte 32.
The client type is
[`dl_token.hh`](../../3hs-frontend/include/nblib/objects/dl_token.hh).

## Parsing and validation guidance

A parser should validate the four-byte magic before interpreting a payload,
then bounds-check the declared header, element, and blob sizes using checked
arithmetic. Element header sizes must be four-byte aligned. Every nonzero blob
offset must be within its current blob, every string must terminate before the
blob ends, and every embedded array must fit within the remaining parent blob.
Do not use an offset from one array level against another level's blob.

The current client parser reports missing input, magic mismatch, truncated
input, and unaligned input through `nb::StatusCode`. Its container-level checks
are visible in the linked `single_object.hh`, `array.hh`, and `raw_array.hh`
implementations. New parsers should still perform the complete pointer and
string bounds validation described above before dereferencing data.

## Compatibility and evolution

The numeric layout, field widths, magic values, and offset bases are protocol
contracts. Reordering fields or changing an existing header size breaks clients
that map the bytes to the current nblib structures. Additive evolution should
use a new payload version or an explicitly negotiated extension rather than
silently changing these records.

`x-minimum` is the server's current compatibility gate. It does not make an
unknown layout self-describing: clients must still recognize the payload magic
and validate the declared sizes. Fields such as aggregate labels and
`content_type` retain the existing client vocabulary; the exact calculations
documented above are specific to this server implementation.
