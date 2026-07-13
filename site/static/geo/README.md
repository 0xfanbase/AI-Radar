# Vendored world map geometry

`ne_110m_admin_0_countries.geojson` is a **build-time input** to
`site/builders/map.py` (the world-map homepage's country outlines). It is
fetched once, committed to the repo, and never fetched at request time or
build time from the network -- matching this project's zero-runtime-
dependency, $0-hosting constraint (see `CLAUDE.md`).

## Source

Natural Earth 1:110m Cultural Vectors, "Admin 0 - Countries" layer,
fetched for real (verified 2026-07-13, HTTP 200, 177 features) from:

    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson

`nvkelso/natural-earth-vector` is the GitHub mirror maintained by
Nathaniel Vaughn Kelso, one of Natural Earth's two founding co-creators,
and is the standard long-lived public distribution point for Natural
Earth's vector layers in ready-to-use GeoJSON (Natural Earth's own site,
naturalearthdata.com, ships shapefiles, not GeoJSON, so this mirror is
the practical way to get this exact dataset as GeoJSON without adding a
shapefile-parsing dependency).

## What was changed before vendoring

The upstream file carries ~60 Natural Earth cartographic/attribute
columns per country (`SOVEREIGNT`, `ADM0_A3`, `LABELRANK`, `POP_EST`,
`GDP_MD`, ...) that `map.py` has no use for. Before committing, every
feature's `properties` object was reduced, in a straight pass with no
geometry edit whatsoever, to just the three fields this build actually
reads:

```
{"name": <NAME>, "iso_a2": <ISO_A2>, "continent": <CONTINENT>}
```

`geometry` (every `Polygon`/`MultiPolygon` coordinate ring) is untouched,
byte-for-byte equivalent to upstream's own coordinates -- this is a
properties-only trim, not a simplification or a re-fabrication of any
geometry. This shrank the vendored file from 838,726 bytes to 257,939
bytes. The one-off trimming step is not itself committed as a script
(it was a single `json.load`/dict-comprehension/`json.dump` pass run
once, over already-fetched real data); if this file is ever refreshed
from upstream, repeat the same properties-only trim.

## License

Natural Earth data is public domain. Per Natural Earth's own terms
(https://www.naturalearthdata.com/about/terms-of-use/): "No permission
is needed to use Natural Earth. Crediting the authors is unnecessary."
No attribution requirement, no usage restriction -- safe for this
project's CC BY 4.0 editorial output and MIT-licensed code alike.

## Known limitation

`map.py`'s projection is a plain equirectangular (linear lon/lat -> x/y)
transform, which distorts area/shape increasingly toward the poles (the
standard, well-understood tradeoff of that projection family, accepted
here per the build plan's "pure math, no new pip dependency" brief in
favor of a true conformal/equal-area projection). Checked directly
against this vendored file before committing: no ring in any feature's
geometry jumps more than 180 degrees in longitude between consecutive
points other than Antarctica's own bottom closing edge (180,-90) ->
(-180,-90), which is an expected south-pole wraparound, not a dateline
defect -- Natural Earth's own upstream data already splits
antimeridian-spanning countries (Russia, Fiji, ...) into separate
east/west polygon pieces, so no stray horizontal line artifact appears
at the +/-180 degree line for any of the ~13 company markers or any
other country this build renders.
