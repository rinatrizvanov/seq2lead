# Third-party licence texts

Full texts of the licences that apply to the data shipped in this repository.
Which file falls under which licence is set out in `../DATA_LICENSE.draft`.

| File | Applies to |
| --- | --- |
| `CC-BY-3.0.txt` | data curated by BindingDB staff |
| `CC-BY-SA-3.0.txt` | data BindingDB imported from ChEMBL, and anything derived from it |

The project code is licensed separately; see `../LICENSE.draft`.

## What licence these two files are themselves under

They are not under the licence they contain, and the release inventory does not
label them that way. They are Creative Commons' own legal code:

> Legal text (we call this legal code) and Commons deeds: Creative Commons makes
> the legal code of its licenses and the CC0 Public Domain Dedication available
> under the CC0 Public Domain Dedication, as well as the text of all of the
> notices and accompanying text on the license pages. [...] This allows anyone to
> reuse those texts for any purpose; however, CC reserves fully and
> unconditionally all trademark and branding rights associated with the licenses,
> the CC0 Public Domain Dedication, and the Commons deeds.
>
> -- <https://creativecommons.org/policies/>

So the inventory records them as `CC0-1.0`, with a `licence_exception` saying why.
Two consequences, both observed here:

1. **They are shipped because they must be.** CC BY 3.0 and CC BY-SA 3.0 section
   4(a) each require that a copy of the licence, or its URI, accompany every copy
   of the Work distributed.
2. **They are never edited.** CC states that if you change the text of a CC
   licence "you may no longer refer to it as a Creative Commons or CC license,
   and you must not use any CC trademarks". The digests below are how that is
   checked.

## Provenance

Both texts were retrieved verbatim from creativecommons.org, not reconstructed:

| File | Source | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `CC-BY-3.0.txt` | <https://creativecommons.org/licenses/by/3.0/legalcode.txt> | 19,467 | `e6bc9e9c474700b708f568bac9e5a8a9bcb2b1dad53442f5ba449fcb848b8e76` |
| `CC-BY-SA-3.0.txt` | <https://creativecommons.org/licenses/by-sa/3.0/legalcode.txt> | 22,240 | `3f941b3b89cf7b8370ceb83cc76d2120d471b58735d8ca60238a751a48d7f72f` |

