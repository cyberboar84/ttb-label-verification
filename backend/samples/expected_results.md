# Expected results for sample labels

- **old_tom_good.jpg** → PASS
- **stones_throw_caps.jpg** → PASS (Dave's case: STONE'S THROW vs Stone's Throw resolves via fuzzy match)
- **silver_creek_titlecase_warning.jpg** → MISMATCH on warning (title case, not all caps — Jenny's catch)
- **copper_ridge_no_warning.jpg** → MISSING warning (must be rejected)
- **harbor_gin_wrong_abv.jpg** → MISMATCH on alcohol_content (application 40% vs label 47%)
- **hopworks_ipa_beer_no_abv.jpg** → PASS (beer: alcohol content is optional for malt beverages)
- **silverleaf_cabernet_wine.jpg** → PASS (wine: ABV required and present)
- **old_tom_tilted.jpg** → PASS (rotated + dimmed; tests preprocessing + model robustness)
