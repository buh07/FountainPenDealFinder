# FountainPenProject

## 1. Objective

Build a personal deal-finding system for Japanese fountain-pen marketplaces that:

1. Collects listings from Yahoo! JAPAN Auctions, Yahoo! JAPAN Flea Market, Mercari, and Rakuten Rakuma.
2. Classifies each pen from listing text + images into a normalized pen identity.
3. Estimates resale value from historical sales data.
4. For Yahoo! JAPAN Auctions, also estimates likely final auction price and low-end possible win price.
5. Applies proxy-service fee and coupon logic for Buyee, FromJapan, and Neokyo.
6. Ranks listings by expected flat profit and percent profit.
7. Produces two daily output lists:
   - **Confident Good Deals**
   - **Potential Good Deals**

This design assumes the system is for **your personal use**. If you ever expand it to a broader user-facing product, you should revisit marketplace terms, anti-bot controls, data retention rules, and account-security design.

---

## 2. Product Definition

### 2.1 Primary use case
You want a ranked shortlist of underpriced fountain pens that are realistically worth buying for resale or collection, with enough structure that you can act quickly.

### 2.2 Target outputs
Each surfaced result should contain:

- `classification`
- `condition_summary`
- `item_count_estimate`
- `items` (for multi-pen lots)
- `marketplace`
- `listing_title`
- `listing_url`
- `seller_id` if available
- `listing_type` (`auction` or `buy_now`)
- `current_price_jpy`
- `estimated_total_buy_cost_jpy`
- `estimated_resale_price_jpy`
- `expected_profit_jpy`
- `expected_profit_pct`
- `confidence`
- `auction_low_win_price_jpy` (Yahoo Auctions only)
- `auction_expected_final_price_jpy` (Yahoo Auctions only)
- `recommended_proxy` (`Buyee`, `FromJapan`, `Neokyo`, or `None`)
- `time_remaining` / `listed_at`
- `deal_bucket` (`confident` or `potential`)
- `rationale`

### 2.3 Business rules

#### Yahoo Auctions
Include items:
- ending within the next 24 hours
- and passing your minimum score/profit threshold

#### Mercari / Rakuma / Yahoo Flea Market
Include items:
- listed during the current day window
- and passing your minimum score/profit threshold

#### Deal buckets
A practical first version:

**Confident Good Deals**
- confidence >= 0.75
- expected profit pct >= user threshold
- expected profit jpy >= user threshold
- classification quality >= minimum threshold
- condition extraction quality >= minimum threshold
- no major risk flags

**Potential Good Deals**
- confidence between 0.45 and 0.75
- expected profit still above threshold
- but one or more uncertainty sources exist
  - image quality poor
  - nib status unclear
  - suspected damage risk
  - model ambiguity
  - coupon uncertainty
  - auction prediction variance high

All thresholds should be configurable.

---

## 3. Core Design Decision: API-first where possible, MCP/browser automation where necessary

### 3.1 Why this matters
This project depends on four marketplaces plus three proxy services. In practice, the cleanest architecture is **hybrid**:

- **Official APIs** where available and allowed
- **MCP servers** for browser automation, extraction, and human-in-the-loop review where no practical buyer API exists
- **Internal normalized API** that hides source-specific details from the rest of your system

### 3.2 Recommended integration principle
Treat every external site as a **connector** behind a common internal contract:

```ts
interface ListingSourceAdapter {
  search(query: SearchQuery): Promise<RawListing[]>
  fetchListingDetail(sourceId: string): Promise<RawListingDetail>
  fetchListingImages(sourceId: string): Promise<ImageRef[]>
  getFreshWindowListings(window: TimeWindow, category: string): Promise<RawListing[]>
}
```

The rest of the system should never care whether a listing came from:
- an official REST API
- a GraphQL API
- a headless-browser flow
- or an MCP tool calling Playwright

That separation is one of the most important design choices in the whole project.

---

## 4. Proposed System Architecture

## 4.1 High-level components

1. **Source ingestion layer**
   - Marketplace adapters
   - Proxy/coupon adapters
2. **Normalization layer**
   - Canonical listing schema
   - Currency/fee normalization
3. **Classification pipeline**
   - Text parser
   - Image classifier / VLM step
   - Canonical pen taxonomy mapper
4. **Pricing intelligence**
   - Pen resale model from r/Pen_Swap history
   - Yahoo Auctions closing-price model
5. **Opportunity engine**
   - Cost calculator
   - Profit calculator
   - Confidence combiner
   - Deal bucketing
6. **Storage**
   - Listings DB
   - Image/object storage
   - Historical feature store
   - Model artifacts
7. **Delivery layer**
   - Daily markdown report
   - Email/Telegram/Discord/Slack optional
   - Manual review UI optional
8. **Operations**
   - Scheduler
   - Monitoring
   - Audit logs
   - Rate-limit and anti-breakage controls

## 4.2 Suggested deployment shape

### Minimum viable production
- 1 app server / worker host
- managed Postgres
- object storage for images
- optional Redis queue/cache
- 1 browser automation pool for MCP/Playwright jobs

### Preferred logical services
- `collector-service`
- `classifier-service`
- `pricing-service`
- `deal-engine-service`
- `report-service`
- `mcp-browser-service`

For a solo project, these can start as modules in one repo and later split only if needed.

---

## 5. MCP Server Design

### 5.1 Why use MCP here
MCP is useful because this project involves tools, not just prompts:

- browser actions
- listing extraction
- coupon page inspection
- image downloads
- fee calculators
- report generation
- manual buy-review workflows

Use MCP as the **tool contract** between the agent layer and your project services.

### 5.2 Recommended MCP servers

#### A. Marketplace Browser MCP
Purpose:
- search supported marketplaces
- open listing pages
- extract visible details
- take screenshots
- collect image URLs
- handle sites without stable public APIs

Suggested tools:
- `search_listings(marketplace, keyword, filters)`
- `get_listing_detail(marketplace, listing_id_or_url)`
- `get_listing_images(marketplace, listing_id_or_url)`
- `get_new_listings(marketplace, since_ts, category)`
- `get_ending_auctions(since_ts, until_ts, category)`

Implementation options:
- Playwright-based MCP server
- Browser-use style MCP tooling
- Custom Node.js MCP server wrapping Playwright

#### B. Coupon/Proxy MCP
Purpose:
- inspect Buyee, FromJapan, and Neokyo fee pages
- inspect coupon pages
- normalize campaign applicability

Suggested tools:
- `list_proxy_options(listing_url, marketplace)`
- `get_proxy_fee_schedule(proxy_name)`
- `get_active_coupons(proxy_name, marketplace)`
- `estimate_proxy_total(proxy_name, item_price_jpy, domestic_ship_jpy, intl_profile)`

#### C. Classification MCP
Purpose:
- expose image/text classification utilities to the agent

Suggested tools:
- `classify_pen_from_listing(title, description, image_urls)`
- `explain_classification(listing_id)`
- `get_top_candidate_classifications(listing_id)`

#### D. Pricing MCP
Purpose:
- expose valuation models

Suggested tools:
- `predict_resale_price(classification, condition, market_context)`
- `predict_yahoo_auction_final_price(listing_features)`
- `predict_yahoo_auction_low_win_price(listing_features)`

#### E. Deal Scoring MCP
Purpose:
- compute expected ROI after proxy costs and uncertainty

Suggested tools:
- `score_listing(listing_id, policy_name)`
- `rank_candidates(date_window, thresholds)`
- `explain_score(listing_id)`

### 5.3 MCP transport recommendation
Use **stdio** for local development and **streamable HTTP** only if you later need remote clients. For this project, local stdio MCP is simpler and less fragile.

---

## 6. External Integrations: reality check by source

## 6.1 Yahoo! JAPAN Auctions
Use cases:
- active auctions
- current bid / start price
- time remaining
- bid count
- seller metadata
- listing images and description

Design recommendation:
- Prefer a stable programmatic feed if you can obtain one.
- Otherwise use a Playwright/MCP browser adapter.
- Cache aggressively because auction listings do not need second-by-second refresh for your workflow.

Special logic needed:
- monitor ending-soon items
- distinguish buy-now / fixed-price from bidding format
- capture bid history signals if visible and allowed
- re-poll high-priority auctions more frequently near closing

## 6.2 Yahoo! JAPAN Flea Market
Use cases:
- new daily listings
- fixed-price purchase listings

Design recommendation:
- same pattern as above: adapter abstraction first
- likely browser extraction unless you have an approved data source

## 6.3 Mercari
Use cases:
- new daily listings
- listing metadata and images

Design recommendation:
- isolate Mercari-specific auth/request behavior behind its own adapter
- assume browser or unofficial request emulation may break over time
- build automated parser tests against saved fixtures

## 6.4 Rakuten Rakuma
Use cases:
- new daily listings
- listing metadata and images

Design recommendation:
- same as Mercari: adapter isolation, fixture-based regression tests, and graceful degradation if the source layout changes

## 6.5 Proxy services: Buyee, FromJapan, Neokyo
Use cases:
- service fees
- domestic shipping assumptions
- coupon applicability
- international shipping heuristics
- possible proxy-specific access to certain shops/marketplaces

Design recommendation:
- do **not** hardcode coupon logic directly into deal scoring
- keep a separate `proxy_pricing_policy` table and `coupon_rule_engine`
- version all fee rules and coupon rules with start/end timestamps

---

## 7. Data Model

## 7.1 Canonical listing schema

```json
{
  "listing_id": "internal-uuid",
  "source": "yahoo_auctions",
  "source_listing_id": "native-id",
  "url": "https://...",
  "title": "Pilot Namiki Yukari...",
  "description_raw": "...",
  "images": ["..."],
  "seller_id": "...",
  "seller_rating": 98.7,
  "listing_format": "auction",
  "price_current_jpy": 42000,
  "price_buy_now_jpy": null,
  "domestic_shipping_jpy": 1200,
  "bid_count": 6,
  "listed_at": "2026-04-05T01:22:00Z",
  "ends_at": "2026-04-06T11:30:00Z",
  "location_prefecture": "Tokyo",
  "condition_text": "目立った傷や汚れなし",
  "lot_size_hint": 1,
  "raw_attributes": {}
}
```

## 7.2 Canonical classification schema

```json
{
  "classification_id": "pilot_custom_urushi_vermilion_medium_nib",
  "brand": "Pilot",
  "line": "Custom Urushi",
  "subtype": "Cartridge/Converter",
  "finish": ["Urushi"],
  "special_features": [],
  "nib_material": "18k",
  "nib_size": "M",
  "body_material": "ebonite",
  "condition_grade": "B+",
  "condition_flags": ["light_surface_wear"],
  "completeness_flags": ["box_missing", "converter_included"],
  "taxon_version": "v1.1"
}
```

For multi-pen listings, classification must operate at **two levels**:

1. **Listing level**
   - estimated lot size
   - whether the lot is homogeneous or mixed
   - whether the price appears to be for the whole lot or per item
2. **Item level**
   - one normalized classification record per visible/mentioned pen
   - one condition record per pen when separable

Example multi-item structure:

```json
{
  "listing_id": "...",
  "lot_type": "mixed_multi_pen",
  "item_count_estimate": 3,
  "items": [
    {
      "item_index": 0,
      "classification_id": "pilot_custom_743_black_14k_m",
      "condition_grade": "B",
      "condition_flags": ["micro_scratches"],
      "visibility_confidence": 0.93
    },
    {
      "item_index": 1,
      "classification_id": "sailor_1911_large_black_21k_f",
      "condition_grade": "C+",
      "condition_flags": ["plating_wear", "nib_alignment_unclear"],
      "visibility_confidence": 0.71
    },
    {
      "item_index": 2,
      "classification_id": "unknown_fountain_pen",
      "condition_grade": "unknown",
      "condition_flags": ["insufficient_image_coverage"],
      "visibility_confidence": 0.38
    }
  ]
}
```

## 7.3 Deal-evaluation schema

```json
{
  "listing_id": "...",
  "classification_id": "...",
  "condition_grade": "B+",
  "item_count_estimate": 1,
  "resale_pred_jpy": 86000,
  "resale_ci_low_jpy": 73000,
  "resale_ci_high_jpy": 95000,
  "auction_low_win_jpy": 47000,
  "auction_expected_final_jpy": 56000,
  "best_proxy": "Buyee",
  "best_total_cost_jpy": 51500,
  "expected_profit_jpy": 34500,
  "expected_profit_pct": 0.669,
  "confidence_overall": 0.79,
  "bucket": "confident",
  "risk_flags": ["nib_size_not_visually_confirmed"]
}
```

---

## 8. Classification System

## 8.1 Problem statement
You do not want generic categories like “Pilot pen” or “Namiki pen.” You need a specific normalized identity usable for pricing.

### Recommended target granularity
A classification should ideally encode:
- brand
- product line / family
- major size variant
- finish/material family
- special craftsmanship markers
- nib material and nib size if inferable
- cartridge/converter vs piston etc. if relevant to pricing

Example:
- `namiki_yukari_royale_urushi_black_18k_m`
- `sailor_king_of_pen_maki_e_crane_21k_b`
- `platinum_3776_century_bourgogne_14k_f`

## 8.2 Pipeline design
Use a **multi-stage classifier**, not one giant prompt. Condition and multi-item decomposition should be first-class outputs, not afterthoughts.

### Stage 1: text extraction
From title/description, extract:
- brand candidates
- line candidates
- finish keywords
- keywords like `蒔絵`, `漆`, `万年筆`, `中字`, `18K`, `14K`, `M`, `F`
- condition signals
- accessory signals (`box`, `converter`, `papers`)
- lot signals (`まとめ`, `セット`, `万年筆 2本`, `3本`, `まとめ売り`)
- references to defects, repairs, personalization, cracks, leaks, corrosion, or missing parts

Use:
- deterministic regex/keyword extraction for high-value tokens
- Japanese normalization
- dictionary-based alias handling

### Stage 2: image understanding
Use a vision model or custom CNN/embedding retrieval to identify:
- brand logos
- clip shapes
- nib engravings
- urushi / maki-e visual features
- body silhouette and cap bands
- visible count of distinct pens in the lot
- per-pen segmentation or detection boxes
- condition evidence such as cracks, plating wear, dents, trim loss, lacquer damage, or bent nibs
- pattern match against known exemplars

### Stage 3: listing decomposition
Before final taxonomy resolution, decide whether the listing contains:
- a single pen
- multiple copies of the same pen
- a mixed lot with multiple distinct pens
- a fountain pen plus non-pen accessories

Recommended output:
- `item_count_estimate`
- `lot_type`
- per-item image regions or references
- confidence that all pen-like objects were detected

### Stage 4: taxonomy resolver
Combine text and image evidence into one canonical class **per detected pen**.

Recommended output:
- top 3 candidate classes per item
- probability for each
- extracted evidence per class

### Stage 5: condition resolver
Generate a structured condition assessment per item.

Recommended outputs:
- coarse grade: `A`, `B+`, `B`, `C`, `Parts/Repair`, or equivalent
- normalized condition flags
- confidence that condition is observable rather than seller-claimed only
- whether the defect is cosmetic, functional, or unknown

### Stage 6: uncertainty tagging
Produce structured uncertainty such as:
- `brand_uncertain`
- `line_uncertain`
- `nib_size_missing`
- `finish_suspected_not_confirmed`
- `condition_claim_only`
- `possible_hairline_crack`
- `possible_fake`
- `possible_parts_mismatch`
- `possible_missing_item_in_lot_count`

## 8.3 Taxonomy storage
Create a hand-curated fountain-pen taxonomy table.

Tables:
- `brand`
- `pen_model`
- `model_alias`
- `finish`
- `special_feature`
- `nib_spec`
- `condition_taxonomy`
- `damage_flag_taxonomy`
- `classification_template`

Do not rely purely on model outputs for taxonomy. The taxonomy itself should be explicit and versioned.

## 8.4 Recommended model strategy
Use a hybrid approach:

### Deterministic first
- regex
- keyword rules
- dictionary matching
- OCR on nib engravings only when useful
- lot-count parsing from title/description
- condition-phrase parsing with Japanese defect vocabulary

### ML second
- CLIP-like image embedding retrieval against your labeled pen-image corpus
- compact text classifier for brand/line prediction
- lightweight object detector / segmenter for multiple pens in one listing
- condition classifier that scores visible wear and damage classes
- optional vision-language model for difficult edge cases

### Why this is better
Pure LLM/VLM classification will be expensive, inconsistent, and hard to calibrate. Deterministic extraction plus a small model will be cheaper and more stable.

---


## 8.5 Condition taxonomy recommendation
Use a normalized condition scale that separates **seller claim**, **visible evidence**, and **predicted functional state**.

Recommended fields:
- `seller_condition_claim_raw`
- `condition_grade_normalized`
- `condition_flags`
- `functional_status` (`working`, `likely_working`, `untested`, `likely_faulty`, `parts_repair`)
- `observability_score`

Recommended normalized condition flags:
- `micro_scratches`
- `deep_scratches`
- `dent_or_ding`
- `trim_wear`
- `plating_wear`
- `cap_band_damage`
- `clip_damage`
- `hairline_crack`
- `thread_damage`
- `barrel_staining`
- `nib_tipping_unclear`
- `bent_nib_possible`
- `misaligned_tines_possible`
- `feed_issue_possible`
- `urushi_damage`
- `maki_e_wear`
- `name_engraving`
- `missing_converter`
- `missing_box`

For expensive pens, condition should materially alter both valuation and confidence. A high-end Namiki with possible urushi damage should be routed to manual review even when the price looks attractive.

## 8.6 Multi-item lot handling
Multi-pen listings should not be scored with the same logic as single-item listings.

Recommended process:
1. detect item count from title, description, and images
2. segment likely pen instances in the image set
3. assign one provisional class per pen
4. decide whether the lot is homogeneous or mixed
5. compute value as:

```text
lot_value = sum(item_expected_resale_values) - lot_uncertainty_discount
```

Where `lot_uncertainty_discount` increases when:
- one or more pens are unidentified
- per-item condition cannot be separated
- accessories are shared ambiguously
- image coverage is incomplete

For the first release, mixed lots should usually land in **Potential Good Deals** unless every pen is clearly identified and condition is reasonably observable.

## 9. Historical Data and Training Sets

## 9.1 r/Pen_Swap dataset
You described a model trained on compiled Reddit sales data. That is reasonable as a valuation signal, but the raw data will be noisy.

Fields to capture from historical posts:
- brand
- model
- special finish
- condition
- explicit defect mentions
- nib size
- asking price
- sold price if known
- accessories included
- whether the sale was a single pen or bundle
- region/currency
- timestamp
- confidence that parse is correct

### Important note
Reddit sale data is useful for resale estimation, but it is **not perfectly aligned** to Japanese domestic-market pricing. You should include market/time adjustments.

## 9.2 Yahoo Auctions historical dataset
For the auction model, capture:
- start price
- current price over time if available
- final sold price
- bid count
- watchers/favorites if available
- seller score
- ending time/day
- category
- text/image-based classification
- condition flags
- domestic shipping terms
- whether reserve-like behavior exists

## 9.3 Gold labels for classification
You will need a manually verified seed dataset.

Recommended starter target:
- 1,000 to 3,000 manually labeled listings across your most frequent pen families
- over-sample expensive and easily confusable classes
- include fakes/parts pens/damaged pens as special negative classes
- include multi-pen lots and bundle listings as a separate labeling task

Without this, your classifier will look better in demos than in reality.

---

## 10. Pricing Models

## 10.1 Resale price model
Goal:
Predict what the pen could sell for in your downstream resale market.

### Inputs
- canonical classification
- condition
- completeness (box/papers/converter)
- cosmetic damage flags
- functional defect flags
- whether the listing is a single pen or bundle
- nib size desirability proxy
- sale month / year
- market source
- exchange rate snapshot if cross-market normalization matters

### Candidate modeling approaches

#### V1
- gradient boosting regressor (LightGBM / XGBoost)
- quantile regression or conformal prediction for confidence intervals

#### V2
- two-stage model:
  1. comparable-sale retrieval
  2. residual regressor on top

Recommended starting point: **retrieval + gradient boosting**, because you will want explanations.

### Outputs
- `predicted_resale_price_jpy`
- `p10_resale_price_jpy`
- `p50_resale_price_jpy`
- `p90_resale_price_jpy`
- `valuation_confidence`

## 10.2 Yahoo Auctions final-price model
Goal:
Predict:
- **lowest plausible winning price**
- **expected final price**

### Separate these into two targets
Do not force one model to output both.

#### Model A: expected closing price
Inputs:
- classification
- current price
- start price
- bid count
- time remaining
- seller rating
- text/image quality score
- day/time ending
- recent category demand

#### Model B: lower-tail plausible win price
This is harder. Use either:
- quantile regression for low percentile outcomes
- or a distribution model over closing prices

Outputs:
- `expected_final_price_jpy`
- `low_tail_price_jpy` (for example 10th percentile)
- confidence/calibration metric

## 10.3 Condition-adjustment model
A highly valuable separate model:
- given a canonical pen class and parsed condition, estimate value penalty
- estimate bundle discount or unidentified-item discount for mixed lots

Examples:
- surface scratches
- plating wear
- nib damage
- missing converter
- lacquer damage
- personalization / engraving

This model can be simple and rule-based at first.

---

## 11. Confidence Design

Do not publish a single confidence score without defining what it means.

Split confidence into components:

- `classification_confidence`
- `condition_confidence`
- `lot_decomposition_confidence`
- `valuation_confidence`
- `auction_confidence`
- `coupon_confidence`
- `listing_quality_confidence`

Then compute:

```text
overall_confidence = weighted_function(
  classification_confidence,
  condition_confidence,
  lot_decomposition_confidence,
  valuation_confidence,
  auction_confidence,
  coupon_confidence,
  listing_quality_confidence
)
```

### Practical weighting suggestion
- classification: 25%
- condition: 15%
- lot decomposition: 10%
- resale valuation: 25%
- auction prediction: 15%
- coupon/fee certainty: 5%
- listing quality / missingness: 5%

Calibrate these after observing false positives.

---

## 12. Proxy, Coupon, and Total-Cost Engine

## 12.1 Why total-cost accuracy matters
Gross underpricing is not enough. Your real decision variable is:

```text
expected_profit = estimated_resale_price - estimated_total_buy_cost
```

Where:

```text
estimated_total_buy_cost =
  item_price
+ domestic_shipping
+ proxy_fee
+ proxy_optional_plan_fee
+ estimated_international_shipping
+ payment fee if applicable
+ customs buffer if you choose to include it
- coupon_discount
```

## 12.2 Proxy engine inputs
- marketplace
- item price
- domestic shipping
- package size / weight estimate
- destination country
- proxy eligibility
- active coupon set
- coupon exclusions

## 12.3 Coupon-rule modeling
Model coupons as data, not code.

Example schema:

```json
{
  "proxy": "Buyee",
  "coupon_id": "rakuten_2604_servicefee50_item10",
  "applies_to_marketplaces": ["rakuten"],
  "discount_type": "service_fee_percent",
  "discount_value": 0.50,
  "min_spend_jpy": 0,
  "start_at": "2026-04-01T00:00:00+09:00",
  "end_at": "2026-04-30T23:59:59+09:00",
  "stackable": false,
  "notes": "item-price coupon handled separately"
}
```

## 12.4 Proxy recommendation logic
For each candidate listing, compute totals for:
- Buyee
- FromJapan
- Neokyo
- direct/no proxy if available

Return the minimum-cost valid route.

### But also include friction rules
The cheapest route is not always the best if:
- coupon requires first-time-user status
- proxy does not support that marketplace path cleanly
- shipping estimate uncertainty is very high

Recommended final output:
- `best_proxy_by_expected_cost`
- `best_proxy_by_risk_adjusted_cost`

---

## 13. Deal Scoring Logic

## 13.1 Core formulas

```text
flat_profit = estimated_resale_price - estimated_total_buy_cost
profit_pct = flat_profit / estimated_total_buy_cost
```

For auctions, compute both:

```text
profit_if_low_win = estimated_resale_price - total_cost_at_low_win
profit_if_expected_final = estimated_resale_price - total_cost_at_expected_final
```

## 13.2 Risk-adjusted profit
Use this for ranking:

```text
risk_adjusted_profit = expected_profit * overall_confidence
```

or

```text
risk_adjusted_profit = expected_profit - uncertainty_penalty_jpy
```

where uncertainty penalty could include:
- classification ambiguity
- poor condition visibility
- coupon uncertainty
- auction variance
- likely fake/repair risk

## 13.3 Recommended ranking outputs
Produce at least three sortable views:
- highest flat profit
- highest percent profit
- highest risk-adjusted profit

Your daily report can still show only two buckets while internally keeping all three rankings.

---

## 14. Scheduling and Refresh Strategy

## 14.1 Daily jobs

### Midnight or early-morning JST
- pull new fixed-price listings from:
  - Yahoo Flea Market
  - Mercari
  - Rakuma
- refresh proxy fee/coupon state
- score and compile daily report

### Rolling Yahoo Auctions monitor
- every 30 to 60 minutes: scan ending-within-24h auctions
- every 5 to 10 minutes for high-priority candidates ending within 2 hours

## 14.2 Priority queue strategy
Not all listings deserve the same polling frequency.

Priority score can be based on:
- estimated value
- current underpricing signal
- closing soon
- classification confidence
- rarity of pen class

This will cut your scraping/API usage significantly.

---

## 15. Storage Design

## 15.1 Postgres tables
Recommended tables:
- `raw_listing`
- `listing_snapshot`
- `listing_image`
- `classification_result`
- `valuation_prediction`
- `auction_prediction`
- `proxy_option_estimate`
- `coupon_rule`
- `deal_score`
- `report_run`
- `report_item`
- `manual_review`
- `training_example`
- `taxonomy_*`

## 15.2 Object storage
Use object storage for:
- original listing screenshots
- downloaded listing images
- thumbnail derivatives
- saved HTML/page captures for debugging
- model artifacts

## 15.3 Why snapshots matter
Do not overwrite listing state. Keep snapshots so you can later answer:
- what the auction looked like 10 hours before closing
- whether the title/price changed
- whether the model prediction was wrong because the listing changed

This is critical for model improvement.

---

## 16. APIs you should expose internally

Even if the project is personal, create a small internal API.

## 16.1 Suggested endpoints
- `POST /collect/run`
- `GET /listings?source=&status=&since=`
- `POST /classify/:listingId`
- `POST /predict/resale/:listingId`
- `POST /predict/auction/:listingId`
- `POST /score/:listingId`
- `GET /reports/daily/:date`
- `POST /review/:listingId`
- `POST /retrain/jobs`

## 16.2 Why bother
Because this avoids locking the project into one chat-agent workflow. The MCP layer can call these APIs, but you can also use them from:
- a CLI
- a small dashboard
- tests
- scheduled jobs

---

## 17. User Interface Recommendation

You only need a light UI.

### V1 UI
- daily markdown report
- link per listing
- per-listing explanation block
- optional “open in browser” buttons

### V2 UI
Simple dashboard with:
- filters by marketplace / classification / confidence
- thumbnail gallery
- predicted profit graph
- manual review controls
- false-positive marking
- “watch this auction” action

For this project, a plain web dashboard is more useful than a complex agent chat UI.

---

## 18. Manual Review Workflow

This will improve the project more than another month of model tuning.

Add these actions:
- `confirm classification`
- `correct classification`
- `mark fake/suspicious`
- `mark condition worse than parsed`
- `mark deal was purchased`
- `mark missed because sold too fast`
- `mark not worth it`

Every manual action should write training feedback.

---

## 19. Monitoring and Reliability

## 19.1 What breaks most often
In a project like this, the weakest points are usually:
- source HTML/layout changes
- anti-bot blocks / auth changes
- coupon-page structure changes
- classification drift for rare pens
- auction model drift during demand spikes

## 19.2 Monitors to add
- source adapter success rate
- parse-field completeness rate
- screenshot diff alarms for key pages
- model confidence drift
- false-positive rate by marketplace
- report count anomalies by day

## 19.3 Alert examples
- Mercari adapter extraction success fell below 80%
- Yahoo Auctions listing parser missing `ends_at`
- coupon count for Buyee dropped to zero unexpectedly
- classification confidence average dropped 20% week over week

---

## 20. Security and account hygiene

Because this project may touch marketplace accounts and proxy accounts:
- use read-only flows wherever possible
- do not store real account passwords in code or prompts
- store secrets in a secrets manager or encrypted env store
- separate scraping/browser identities from your primary account when allowed
- log tool use but redact credentials and cookies

Also separate:
- collector credentials
- model/API keys
- notification credentials

---

## 21. Compliance and risk notes

This is the blunt version:

- Some of the sites involved may not offer clean public buyer APIs for your use case.
- In practice, that means some connectors may need browser automation or other unofficial integration paths.
- Those paths are operationally fragile and may carry policy or account risk depending on how they are used.

So the design should explicitly support:
- connector disablement
- fallback parsing modes
- source-specific rate limits
- human review before action

Do **not** couple the rest of your system to any one fragile integration.

---

## 22. Recommended Tech Stack

## 22.1 Language choices

### Good default
- **Python** for ML, ranking, parsing, orchestration
- **TypeScript/Node.js** for MCP servers and browser automation

This is the stack I recommend.

## 22.2 Suggested components
- Python: FastAPI, Pydantic, pandas, scikit-learn, LightGBM, PyTorch if needed
- Node.js: Playwright, MCP SDK, zod, undici
- Postgres: Supabase/Postgres or managed PostgreSQL
- Object storage: Cloudflare R2 or S3-compatible storage
- Queue/cache: Redis or Upstash Redis
- Scheduler: cron, GitHub Actions, or a worker scheduler
- Notifications: Telegram bot / email / Discord webhook

## 22.3 Why not overbuild
You do not need Kubernetes for this. One app service plus one browser worker is enough initially.

---

## 23. Suggested Repository Structure

```text
fountain-pen-project/
  apps/
    api/
    worker/
    dashboard/
    mcp-browser/
    mcp-pricing/
  packages/
    taxonomy/
    source-adapters/
    scoring/
    feature-extraction/
    common-types/
  models/
    resale/
    yahoo-auction/
    image-retrieval/
  data/
    fixtures/
    taxonomy/
    labeled/
  infra/
    docker/
    terraform/
  docs/
    FountainPenProject.md
```

---

## 24. Example End-to-End Flow

1. Scheduler starts daily run.
2. Source adapters fetch:
   - Yahoo Auctions ending within 24h
   - Mercari/Rakuma/Yahoo Flea listings posted today
3. Raw listing data is normalized.
4. Images are downloaded and stored.
5. Classification pipeline produces canonical pen identity + uncertainty.
6. Resale model predicts sell price distribution.
7. Yahoo auction model predicts low win and expected close where relevant.
8. Proxy engine evaluates Buyee / FromJapan / Neokyo routes and coupons.
9. Deal engine computes flat profit, percent profit, risk-adjusted profit.
10. Listings are bucketed into `confident` and `potential`.
11. Report generator creates markdown and optional dashboard entries.
12. You review top candidates and mark outcomes.
13. Feedback is stored for retraining and rule tuning.

---

## 25. Implementation Roadmap

## Phase 0: taxonomy and fixtures
Deliverables:
- pen taxonomy v1
- 200 manually curated example listings
- fixture snapshots for each source

## Phase 1: ingestion MVP
Deliverables:
- source adapters for all four marketplaces
- raw listing DB
- screenshot + image capture
- daily collector jobs

Success criterion:
- stable extraction of listing title, price, time, description, images

## Phase 2: classification MVP
Deliverables:
- text extractor
- image retrieval classifier
- manual review UI
- confidence framework

Success criterion:
- useful normalized class for top target pen families

## Phase 3: pricing MVP
Deliverables:
- resale model from r/Pen_Swap historical data
- Yahoo auction expected/floor models
- calibration dashboard

Success criterion:
- model intervals are directionally trustworthy, not just point predictions

## Phase 4: proxy/coupon engine
Deliverables:
- proxy fee rules
- coupon rule parser
- best-route recommender

Success criterion:
- landed cost estimate is reliable enough for action

## Phase 5: ranking and reporting
Deliverables:
- confident vs potential buckets
- markdown report
- alerting/notifier integration

## Phase 6: feedback loop
Deliverables:
- manual correction workflow
- retraining pipelines
- drift monitoring

---

## 26. Maintenance burden you should expect

This project is not “set and forget.” The main recurring work is:

### Weekly / biweekly
- fix source parsers when pages change
- review false positives
- tune coupon rules
- update taxonomy aliases

### Monthly
- retrain resale model if enough new data exists
- recalibrate auction model
- review shipping/fee assumptions

### Quarterly
- prune dead logic
- review source risk and policy changes
- expand labeled data for new pen families

### Realistic maintenance estimate
For a personal system that stays accurate:
- **light-touch mode:** 4 to 8 hours/month
- **actively improved mode:** 10 to 20 hours/month

The browser/source maintenance will usually dominate model maintenance.

---

## 27. Expected infrastructure and API cost

These estimates assume a **single-user personal system** with:
- a few scheduled collection runs per day
- browser extraction for a few thousand listing pages per month
- lightweight ML inference
- modest image storage
- occasional LLM/VLM use for hard edge cases rather than every listing

## 27.1 Recommended low-cost baseline

### Option A: simplest paid setup
- App/worker compute: **$5 to $20/month**
- Managed Postgres: **$0 to $25/month**
- Object storage: **$0 to $5/month**
- Redis/cache/queue: **$0 to $10/month**
- Browser automation host: **$5 to $20/month**
- LLM/VLM/API credits: **$5 to $40/month**

**Expected total:** **about $15 to $80/month**

### Option B: more comfortable production-ish setup
- App server + worker: **$20 to $50/month**
- Managed Postgres: **$25/month**
- Object storage + requests: **$1 to $10/month**
- Redis/cache: **$0 to $15/month**
- Browser worker pool: **$15 to $40/month**
- LLM/VLM/API credits: **$20 to $100/month**

**Expected total:** **about $60 to $240/month**

## 27.2 Where the money actually goes
For this project, the likely cost ranking is:
1. browser automation / compute
2. managed database if you pick convenience over self-hosting
3. LLM/VLM calls if you use them on every listing
4. storage, which should stay cheap

## 27.3 Cost-control recommendations
- do not run VLM/LLM classification on every listing
- use deterministic filters first
- use image embedding retrieval before expensive multimodal reasoning
- only refresh high-priority auctions at high frequency
- keep screenshots only for selected candidates or recent windows
- batch model jobs when possible

## 27.4 Example reference stack and current pricing anchors
These are not the only choices, but they are useful anchor points:

- **Cloudflare Workers** developer platform pricing starts at **$5/month**, with included requests and CPU allowance.
- **Supabase Pro** starts at **$25/month per project**.
- **Cloudflare R2** has a free tier and then usage-based storage/operation pricing with **zero egress fees**.
- **Upstash** offers free and usage-based Redis plans.
- **OpenAI web search** is priced at **$10 per 1,000 calls**, and GPT-5.4 API pricing is usage-based by token.

For this project, you can keep costs low by minimizing expensive tool calls and reserving multimodal reasoning for uncertain listings only.

## 27.5 Practical API-credit estimate
If you use LLM/VLM sparingly:
- taxonomy resolution and difficult classification only
- maybe **200 to 1,000 premium calls/month**

Then a practical budget is often:
- **$5 to $30/month** for text-only support tasks
- **$20 to $100/month** if you regularly do image-heavy multimodal review

The biggest mistake would be routing every listing through a frontier multimodal model.

---

## 28. Recommended first version

If I were implementing this for myself, I would build **this exact V1**:

1. Ingest only the top fountain-pen-related search slices from each marketplace.
2. Use rules + small models to classify only your top target brands first:
   - Pilot / Namiki
   - Sailor
   - Platinum
   - Nakaya
   - Pelikan
   - Montblanc
3. Build a resale model with interpretable comparable-sale retrieval.
4. Build Yahoo expected-close prediction, but keep low-tail prediction conservative.
5. Treat coupons as a separate rules engine.
6. Produce a markdown report and a tiny review page.
7. Capture your corrections aggressively.

That version will beat a broader but less reliable system.

---

## 29. Strong recommendations and blunt cautions

### What I think is most important
- Your **taxonomy quality** matters more than fancy modeling.
- Your **source adapters** are the most fragile part.
- Your **landed-cost calculation** is as important as your resale model.
- Your **manual review loop** will create most of the long-term value.

### What I would avoid early
- end-to-end “one model does everything” pipelines
- overly broad pen coverage on day one
- fully autonomous buying actions
- building too much UI before the scoring is trustworthy

### The hardest technical problem
Not classification.
Not even scraping.

The hardest part is building a **calibrated, trustworthy opportunity score** that handles:
- uncertain classification
- noisy resale data
- changing proxy/coupon costs
- auction variance
- hidden-condition risk

That is where most of your iteration time will go.

---

## 30. Final recommended build plan in one paragraph

Build a hybrid system with source adapters behind a common internal API, MCP servers for browser-driven collection and review, a versioned fountain-pen taxonomy, a multi-stage text+image classifier, an interpretable resale model plus a separate Yahoo auction model, a proxy/coupon rule engine that computes landed cost per service, and a deal scorer that outputs both flat and percent profit with calibrated confidence. Keep the first version narrow, instrument everything, and assume the ingestion layer will require ongoing maintenance.

---

## 31. Source notes for current pricing / platform assumptions

These sources informed the current external-platform assumptions used in the cost/integration section:

- MCP specification and docs: modelcontextprotocol.io
- OpenAI API pricing and model docs: openai.com and developers.openai.com
- Rakuten Web Service docs: webservice.rakuten.co.jp
- Buyee fee/campaign/help pages: buyee.jp and media.buyee.jp
- FromJapan fee/help pages: fromjapan.co.jp
- Neokyo fee/help pages: neokyo.com
- Cloudflare Workers and R2 pricing: cloudflare.com and developers.cloudflare.com
- Supabase pricing: supabase.com
- Upstash pricing: upstash.com

