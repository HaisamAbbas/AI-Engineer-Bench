# Unit normalization API

`POST /normalize` returns a decimal `grams` value. Declared conversions are `kg=1000g`,
`g=1g`, and `mg=0.001g`; comparisons tolerate `0.0001g`. Preserve source evidence,
source unit, and unrelated labels.
