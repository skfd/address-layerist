"""The one place that decides what text a point is labelled with.

Two source columns beside the street number distinguish addresses that would
otherwise draw the same text, and a label that drops either renders distinct
buildings identically (skfd/oakville-address-layer#1):

    unit    townhouse/apartment complexes share one street number, so a whole
            block draws as "2280" repeated unless the unit is in the label.
    suffix  a separate A/B/"1/2" column, so 335 and 335A both draw as "335".

The suffix belongs *to* the number (OSM writes addr:housenumber=335A), so it is
folded in first; the unit is a separate tag and leads, which is both the
Canadian convention ("3-2280 Munn's Ave") and easier to scan down a row of
townhouses because the varying part is on the left:

    number 2280, unit 3    ->  "3-2280"
    number 335,  suffix A  ->  "335A"
    number 2280            ->  "2280"

Both tile builders go through here, so the raster labels and the vector `name`
property (what iD draws) agree wherever a full label is drawn.

One deliberate exception: in a complex too crowded for the full labels to fit --
MIN_COMPLEX_DOORS or more doors sharing one street number -- the raster falls
back to drawing the bare unit and spelling the street number out once, in
colour, beside the cluster (see raster.py).  The vector `name` keeps the full
"3-2441" there, because it is read by click-to-inspect and by iD's own
labeller, neither of which has that anchor label to lean on.  The two disagree
in that one place on purpose, and only where the alternative is a missing label.
"""


def housenumber(number, suffix):
    """Street number with its suffix folded in ("335" + "A" -> "335A").

    Sources are split on whether the suffix is its own column: Toronto's
    ADDRESS_NUMBER already reads "246A" while it *also* publishes LO_NUM_SUF,
    so a city that maps both must not end up with "246AA".
    """
    number = (number or "").strip()
    suffix = (suffix or "").strip()
    if not suffix:
        return number
    if not number:
        return suffix
    if suffix.isalpha():
        # "335"+"A" -> "335A", but leave an already-combined number alone.
        if number.upper().endswith(suffix.upper()):
            return number
        return f"{number}{suffix}"
    # Fractions and the like read better parted: "963" + "1/2" -> "963 1/2".
    return f"{number} {suffix}"


def display_label(number, unit):
    """Label text for an address point. Either part may be empty/None.

    ``number`` is expected to have any suffix already folded in by
    ``housenumber`` -- slim.py does that once, when it writes the slim file.
    """
    number = (number or "").strip()
    unit = (unit or "").strip()
    if not unit:
        return number
    if not number:
        return unit
    # A unit equal to the street number is a coincidence, not an echo: unit 15
    # of 15 Hays Blvd is a real door, and 1,757 rows across the Ontario registry
    # look like that -- thinly spread, never the wholesale column copy that
    # would justify suppressing them. Drawing "15-15" is honest; dropping the
    # unit is the very bug this module exists to fix.
    return f"{unit}-{number}"
