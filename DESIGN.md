# Design note

**Principle.** The LLM suggests, code verifies, a person approves. Nothing the LLM returns is written to the model directly.

**Stopping the LLM inventing things.**
1. *Grounding.* Each reply must be JSON matching a Pydantic schema. Any table or column not in the catalogue is rejected, as are missing or duplicated columns.
2. *Profile contradictions.* A semantic type that cannot sit on the physical type is rejected (money on a date, a flag on text). "No personal data" is rejected when values match a NINO, email, phone, postcode or bank-reference pattern in at least 80% of samples.
3. *Retries with feedback.* Failures go back to the LLM with the exact reasons, up to 3 attempts. Valid columns are kept; anything still failing becomes a clearly marked placeholder at confidence 0.15 instead of a guess.
4. *Metrics are specs, not SQL.* The LLM proposes tables, columns, aggregation and filters. Code compiles the SQL, allows joins only child to parent (no row fan-out), checks filter values exist in the data, then runs it read-only with a timeout. It must return a number before it can be approved.
5. *Privacy.* Samples are masked before the prompt (shape kept, content dropped), min/max is withheld for personal columns, free text is redacted. Every call (prompt, response, tokens, cost, latency, valid or not) is logged.

**Inferring relationships.** Declared keys are read from the catalogue. For the rest, a column is a candidate only if at least 95% of its distinct values exist in a single-column integer primary key. Confidence is 0.45 value containment + 0.35 name support + 0.20 sample size, minus 0.15 per rival parent that also fits. Names alone cannot find `contr_id`, `assr_id` or `mgr_id`, so these land in Medium for a steward. Link tables (composite key of two foreign keys) become many-to-many relationships, `ref_cd` is reference data, and `aud_log.rec_id` is detected as a polymorphic reference through `tbl_nm`. Code overrides the LLM on table kind when these rules fire.

**Confidence.** Never the LLM's self-rating alone. Column = 0.15 self-rating + 0.30 type evidence (type fits the physical type and profile) + 0.20 personal-data agreement with the rule detector + 0.20 name grounding against an abbreviation dictionary the LLM never sees + 0.15 agreement across repeated runs (optional, weights renormalise if off), minus 0.05 per retry. Hover on a badge shows each reason. Edited items keep the original suggestion.

**Storage.** SQLite file: no extra service, transactional edits, easy to inspect. Approved and edited items survive a rescan; a rescan only calls the LLM for tables whose schema changed.

**Left out, and why.** The mock's confidence is optimistic because its names come from the same dictionary used to check them (circular); a real model does not have this problem. Approved relationships are not re-checked for drift on rescan. The glossary merges only near-exact names automatically (similarity 0.9); synonyms like "Given Name" need a steward merge. Metric filters apply to the measure table only. Relationship inference assumes integer surrogate keys with distinct ranges per table: if every table's ids run 1..N, value overlap cannot discriminate and name support has to carry the decision. No auth, no concurrency control on edits.

**Next, given more time.** Re-validate approved items on rescan and flag drift; decode `ref_cd` into allowed values per `*_cd` column; embeddings for glossary matching; evaluation set with precision/recall for LLM output.
