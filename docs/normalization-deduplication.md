# Business normalization and matching

Every accepted candidate creates a `SourceRecord` with the source identity,
source URL, attribution, raw values for the normalized fields, and a payload
fingerprint. Re-imports preserve another observation while reusing the canonical
business when the same source identifier or exact source payload is seen again.
Raw fields are limited to the candidate business fields; connector response
bodies and unrelated provider fields are not retained.

## Normalization

- Names are Unicode-decomposed, case folded, accent folded, and reduced to
  alphanumeric words for `normalized_name`. The display name remains the best
  quality original spelling.
- Domains are parsed from website URLs, lowercased, IDNA encoded, and stripped of
  a leading `www.` and trailing dot. The original selected website value remains
  available on the business and source observation.
- Phone matching keys remove punctuation and normalize international `00` to
  `+`; extensions are retained. This is not country-aware E.164 validation.
- Addresses get a normalized matching key with Unicode/case/punctuation
  normalization. The canonical display address and original observation remain.
- Validated latitude/longitude are rounded to six decimal places in the
  canonical record; raw source coordinates remain in that observation.

## Match classifications

Each source observation receives a persisted `MatchDecision` and a rule name.

- **Exact:** same source plus external ID; same source plus exact payload
  fingerprint if the source omits an external ID; exact normalized name and
  address; or same domain and similar name at the same normalized address or
  within 50 metres.
- **Likely / uncertain:** weighted similarity across name, domain, phone,
  address, city, and proximity, when location evidence does not conflict. These
  decisions are suggestions only; they create separate canonical businesses.
- **Distinct:** explicit address/city conflict, coordinates more than one
  kilometre apart, a score below the uncertain threshold, or no candidate.
  Different addresses keep branches separate even when name and domain match.

Only exact rules automatically reuse a canonical business. Ambiguous equal-score
candidates are classified uncertain. Default likely and uncertain score
thresholds are `0.78` and `0.48`. A reviewed source can tune classification
thresholds in its `Source.config`:

```json
{"matching_rules": {"likely_threshold": 0.82, "uncertain_threshold": 0.5}}
```

Threshold changes do not enable automatic merging of likely or uncertain
matches. Name-only similarity is capped at uncertain because it lacks
corroborating location/contact evidence. Field quality can be configured separately:

```json
{"field_quality": {"name": 80, "website": 90, "phone": 70, "address": 80}}
```

Quality is an integer from 0 to 100; higher-ranked values replace lower-ranked
values, and empty fields can be filled. Equal-ranked non-empty data is retained
to avoid churn. The default rank is 50. Original observations remain traceable
regardless of canonical updates.

## Explicit merge and reversal

An administrator can explicitly merge after review:

```text
POST /businesses/{source_business_id}/merge/{target_business_id}
```

The audit event records the actor, source snapshot, moved observation IDs, and
target fields changed. The response includes `merge_id`. Reverse it with:

```text
POST /merges/{merge_id}/reverse
```

Reversal records the reviewing actor, restores the source business and its source-record links. It restores
target fields changed by that merge only when they still have the merge-written
value, preserving later edits. A merge event can be reversed once.
