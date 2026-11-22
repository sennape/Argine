# R3-04 campaign-efficiency amendment (prospective, additive; acquisition NOT opened)

Written after Gate 2 (MECHANISM_PASS, freeze 59fd7ac6...) and before OPENING_FREEZE and any counted timing launch. The protocol freeze a88359b5... is unchanged.

Recommended design **PRIMARY_PLUS_8G**: 1.5 GiB x 18 blocks (primary, unchanged) + 8 GiB x 6 blocks (negative control) + 1 warmup per budget = 146 physical launches (144 counted). The 4 GiB timing budget (37 launches) is dropped. Validated shutdown helper required.

Expected wall (helper, untraced): 6.8 / 8.0 / 9.4 h (low/central/high); the full design would be 8.2 / 9.8 / 11.5 h.

Files: EFFICIENCY_REVIEW.json, DESIGN_COMPARISON.json, UPDATED_TIME_MODEL.json, CLAIM_IMPACT.md, AMENDMENT.json, FREEZE.json; `validate_amendment.py` recomputes everything; `build_amendment.py` regenerates.
