# CSGO Trader screening experiment — September 22, 2026

## Decision

The saved data shows a useful **outward-leg ranking signal**, but does not establish that the complete two-leg selection policy is meaningfully better than random exploration. It does **not** establish a dependable price-gap cutoff. Do not enable a production cutoff or resolve the feed's fee interpretation based on this experiment.

This was a read-only historical replay of September 20 observations, not a new live randomized collection or a paper trade. No provider requests, trades, predictions, funding changes or production selection changes were made by the experiment.

## Main result

An item counted as useful if detailed prices supported at least one positive-net DMarket round trip with a fixed reference partner, using fully covered buy orders at both selling steps. This means a conditional quote-based estimate, not demonstrated profit or future liquidity. The same $10 capital, per-item fees and depth checks applied to both groups. The existing research policy assumes zero additional operational costs; this optimistic assumption is recorded in `results.json`.

Each replay allocated 60 candidate requests and the same 12 requests for reference partners. Request costs came from the archived attempts associated with each selected response; a missing attempt record reserved at least one request per endpoint. These are replay request budgets, not newly issued requests.

Average results over 500 held-out random orderings:

| Selection policy | Items checked | Useful items: covered buy orders | Useful items: any selling assumption |
|---|---:|---:|---:|
| Uniform random exploration | 17.22 | 10.65 | 13.09 |
| Hint ordering, no cutoff | 15.90 | 9.88 | 10.37 |
| Hint ordering, training-selected 30% priority threshold | 16.49 | 10.88 | 11.97 |

The threshold-based policy gained only **0.22 useful items per budget, about 2.1%**, versus random exploration. It won outright in 41.4% of paired orderings; the middle 95% of paired differences ranged from -3 to +3 useful items. This range describes shuffle variability within this particular sample; it is **not** a confidence interval for the CS2 market. Repeating shuffles does not create new independent market observations.

The best buy-order route estimate found per run averaged $7.59 net under the threshold policy versus $7.68 under random exploration. These are alternative historical estimates using the same capital, not additive earnings and not currently available routes. The ranking signal did not improve this measure of the user's profit objective.

Lowest-ask and midpoint scenarios were calculated separately and never counted as executed sales. Their much larger assumed-price estimates do not establish economic value, so the primary comparison uses covered buy orders.

## What the gap tells us

Under the test-only buyer-price interpretation:

`outward gap = (Steam net derived from summary / DMarket purchase hint − 1) × 100`

`return gap = (DMarket net derived from bid-or-listing hint / Steam summary − 1) × 100`

For example, $1.30 of estimated Steam proceeds after fees versus a $1.00 purchase gives a 30% outward gap. It is a single-leg currency-conversion estimate, not full-route ROI. A losing leg can still form a positive round trip with a strong partner.

Outward results on the held-out sample with usable hints:

| Outward gap | Items | Items forming a positive buy-order route estimate |
|---|---:|---:|
| Below 0% | 41 | 12 |
| 0% to below 30% | 12 | 11 |
| At least 30% | 22 | 20 |

Larger gaps were useful for sorting outward candidates in this sample. However, the 0–30% band was just as productive as the 30%+ band. A hard 30% rejection rule would miss **23 of the 43** positive outward candidates with hints, including 12 with negative hinted gaps. Missing detailed inputs count as unsuccessful investigations in this table, not evidence that an item is intrinsically unsellable.

For the return leg, all five positive held-out candidates with hints had negative return gaps. There are too few positive return examples here to choose a reliable return threshold. Applying the same positive-gap threshold to both directions is particularly poorly supported.

The CSGO Trader summary was within 10% of the detailed highest Steam bid for only 11 of 73 held-out items with both values. Its median was about 32% higher than that bid. This does not identify the feed's fee basis: a historical summary and a later current bid are different measurements.

## Threshold selection and uncertainty

The threshold grid was frozen before calculating route outcomes: -50%, -25%, 0%, 10%, 20%, 30%, 50%, 75%, 100%. Thresholds only controlled priority queues; all candidates remained eligible for random exploration. Every five selections attempted two outward, two return and one exploration item, with deduplication and exploration fallback.

Training used 117 variants from 31 finish families. Evaluation used 79 variants from 23 different finish families. Wear variants, StatTrak and Souvenir versions of a finish stayed together when splitting. Thresholds were chosen on training data only, using 100 orderings; the held-out set was then evaluated over 500 orderings. The later gap-band analysis above is descriptive, not a newly trained rule.

30%, 50% and 75% tied on the primary training measure; the predeclared tie break chose the lowest. **30% is not a uniquely identified optimum.** Treating the summary as seller proceeds instead moved the training winner to the grid boundary, 100%, while the held-out gain stayed negligible: 10.86 versus 10.65 useful items. Neither interpretation was enabled in the bot.

## Evidence and limitations

- Hints were frozen at September 20, 14:45 UTC. The CSGO Trader file had been published at 05:11 UTC and downloaded at 09:27 UTC; later outcomes did not affect candidate scores.
- The frozen catalogue supplied 7,700 fresh DMarket hints. The Steam file contained 34,413 exact names. Hints older than 24 hours were excluded.
- Detailed observations were selected from the following four-hour window. Each item's Steam and DMarket observations had to be within 15 minutes of each other. All usable detailed books were validated at the common window endpoint, 18:45 UTC, against the four-hour freshness limit.
- There were 300 items with a recorded Steam attempt in the window. Excluding three reference partners and 101 items outside the predeclared hint-based affordability screen left 196 variants. All had archived responses from all three endpoints, but some responses were errors or had unusable books. Failures remained in the comparison.
- The cohort is strongly biased: 117 Galil AR variants, 76 G3SG1 variants, one AK-47, one music kit and one capsule. It is not a random sample of the whole market.
- Fixed reference partners were Fracture, Kilowatt and Revolution cases, selected by name before testing, from the existing earlier route basket. Recoil had no detailed check in the window. Routes against these partners do not measure every possible item pair; good opportunities elsewhere can be missed.
- The archived successful Steam responses were normalized by the since-reverted `steam-ssr-variants-v2` parser. Current validation and independent arithmetic checks do not independently prove that old parser's source interpretation. This experiment does not reinstate or qualify Task 4.
- This compares combined Steam/DMarket screening scores with random exploration. It does not isolate the incremental information in CSGO Trader from DMarket price hints alone.
- Quotes were not collected simultaneously across all items and do not reserve availability after the 17-day minimum modeled delay. No completed-sale outcome was used.

## Recommendation

Retain CSGO Trader as a name catalogue. The observed outward ranking signal warrants a broader prospective test after reliable detailed Steam coverage is restored; the present result does not justify activating a cutoff or declaring the whole screening policy better.

For that next test, freeze the policies before collecting outcomes; use a random sample across item categories and price ranges, matched request budgets, both route legs, and multiple collection times. Include the existing no-Steam-summary selector as an additional control so the contribution of CSGO Trader itself can be separated from DMarket hints. Compare positive route estimates and best net gain per request, then choose direction-specific priority thresholds on separate training data. Keep below-threshold items eligible for exploration.

## Files and reproduction

- `protocol.json`: frozen experiment choices and assumptions.
- `run_replay.py`: read-only replay using existing route calculations.
- `evidence.json.gz`: compressed public market observations and request timestamps; no account journal copy.
- `results.json` and `items.csv`: full results, settings, source IDs and candidate outcomes.
- `diagnostics.py` / `diagnostics.json`: descriptive gap bands and family/budget checks.
- `validate_replay.py` / `validation.json`: timing checks, module hashes, independent fee checks for 1–1,000 cents, and independent depth/budget arithmetic for the ten strongest buy-order estimates.

From this directory, with the v2 virtual environment and matching source modules:

```powershell
$v2 = 'C:\Users\Legion\Documents\stuff\repos\arbitrage-bot-v2'
& "$v2\.venv\Scripts\python.exe" -B run_replay.py --repo $v2 --archive evidence.json.gz
& "$v2\.venv\Scripts\python.exe" -B diagnostics.py
& "$v2\.venv\Scripts\python.exe" -B validate_replay.py --repo $v2
```

The archived replay was checked against the read-only live-journal extraction and produced identical results. It requires no network requests. Calculations use the source module hashes recorded in `validation.json`; future pricing changes may change a replay and should be treated as a separate experiment.
