# ClinGen API Findings (live network capture, 2026-06-12)

Captured via Playwright (headless chromium) network monitoring of the 4 SPA URLs,
then direct `curl` probing of the discovered backend APIs.

## Summary table

| Domain | Backend API (discovered) | Bulk behavior | Freshness signal | Per-record date |
|---|---|---|---|---|
| Gene-Disease Validity | `GET https://search.clinicalgenome.org/api/validity` | Returns ALL 3,615 rows (~1.8 MB, ~3.6s); `page/size/search` IGNORED server-side | max(`date`) + total + content hash | `date` (ISO), `released` (MM/DD/YYYY) |
| Gene Dosage | `GET https://search.clinicalgenome.org/api/dosage` | Returns ALL 2,208 rows (~1.45 MB, ~2.6s); params IGNORED | max(`rawdate`) + total + hash | `rawdate` (ISO), `date` (MM/DD/YYYY) |
| Clinical Actionability | `GET https://actionability.clinicalgenome.org/ac/api/summ/brief` | Returns full index array (~484 KB) of all curation docs | max(`metadata.lastUpdated`) + count + hash; per-context `release.date` | `metadata.lastUpdated` (RFC1123) |
| Variant Pathogenicity (ERepo) | `GET https://erepo.clinicalgenome.org/evrepo/api/classifications?format=json` | Paginated via `matchLimit`; `gene=` filters server-side | `news` endpoint `relatedVersion` (e.g. `2.5.6`) + `date` | `publishedDate` per interpretation |

## Endpoint details

### 1. Gene-Disease Validity — `search.clinicalgenome.org/api/validity`
- One request returns the whole table. `?page=1&size=25&search=BRCA1` had no effect (still 3,615 rows). Filtering is client-side in the SPA.
- Rate limit headers: `X-RateLimit-Limit: 60` (per minute). `Cache-Control: no-cache`. No ETag/Last-Modified.
- Row schema: `symbol, hgnc_id, symbol_id, ep` (expert panel), `affiliate_id, disease_name, mondo, moi` (mode of inheritance: AD/AR/XL/...), `sop, classification` (Definitive/Strong/Moderate/Limited/Disputed/Refuted/No Known Disease Relationship/Animal Model Only), `order, perm_id` (`CGGV:assertion_<uuid>-<timestamp>`), `animal_model_only, report_id` (uuid), `released` (MM/DD/YYYY), `date` (ISO 8601).
- Permalink per assertion: `https://search.clinicalgenome.org/kb/gene-validity/<perm_id>` (CGGV id).
- CSV alt download (richer filename, dynamic content): `GET https://search.clinicalgenome.org/kb/gene-validity/download` → `Content-Disposition: filename=Clingen-Gene-Disease-Summary-YYYY-MM-DD.csv` (~1.1 MB). JSON API preferred (has mondo, perm_id, report_id).
- Expert panels reference: `GET https://search.clinicalgenome.org/api/affiliates` → 59 GCEPs/VCEPs with curation counts (`agent, label, curie, count`).

### 2. Gene Dosage — `search.clinicalgenome.org/api/dosage`
- One request returns all 2,208 dosage records. Same param-ignoring behavior.
- Row schema: `type` (0 = gene; region records use other types/ISCA), `symbol, hgnc_id, locus, location, relationship, grch37, grch38` (chr:start-end), `acmgsf` (ACMG SF flag), `pli, hi` (haploinsufficiency %), `haplo_assertion` (0/1/2/3/30/40 score code), `triplo_assertion, omim, omimlink, morbid, omimcombo, plof, date` (MM/DD/YYYY), `rawdate` (ISO), `haplo_disease, haplo_disease_id, haplo_mondo, triplo_disease, triplo_disease_id, triplo_mondo`.
- Dosage scores: 0/1/2/3 = no evidence/little/emerging/sufficient; 30 = autosomal recessive; 40 = dosage sensitivity unlikely.
- Recurrent-CNV/region records and the canonical TSV/BED files are on the ClinGen FTP (see data-sources research) — confirm whether regions are included here or separate.

### 3. Clinical Actionability — `actionability.clinicalgenome.org/ac/api/summ/brief`
- Full index array. Each item: `docId` (e.g. `AC1034`), `iri`, `curationType` (Gene-Condition), `metadata.lastUpdated` (RFC1123) + `lastAuthor`, `disease`, `modesOfInheritance`, `context.Adult`/`context.Pediatric` → `{@id (SEPIO doc IRI), release.date, status{overall,stg1,stg2,scoring,assertion}}`.
- Detail: SEPIO doc at the context `@id`, e.g. `https://actionability.clinicalgenome.org/ac/Adult/api/sepio/doc/AC1034`. NOTE: `/ac/api/doc/AC1034` is 404 (Redmine) — use the SEPIO IRI from the brief.
- CORS open (`Access-Control-Allow-Origin: *`). nginx, no rate-limit headers.

### 4. Variant Pathogenicity / ERepo — `erepo.clinicalgenome.org/evrepo/api/...`
- Per-gene summary counts: `GET /evrepo/api/summary/classifications/summary/gene` → `{data:{GENE:{classifications:{Benign,Likely Benign,VUS,Likely Pathogenic,Pathogenic:counts}}}}` (~29 KB, all genes). Great for discovery/index.
- Variant interpretations (live, filterable): `GET /evrepo/api/classifications?format=json&gene=BRCA1&matchLimit=N`. `gene=` filters server-side. Each: `variationId, condition{@id (MONDO), label}, uuid, caid` (`CAR:CA000895`), `@id` (permalink), `publishedDate`, `hgvs[]`, `evidenceLinks[]` (PubMed), ACMG criteria/classification in fuller payloads. `@context` light vs full.
- `format=tabbed` exists but streamed full export is slow (timed out at 40s with matchLimit) — prefer `format=json` + pagination.
- **Freshness/version**: `GET /evrepo/api/summary/news/` → array of release notes, newest first, each with `date` (ISO), `relatedVersion` (e.g. `2.5.6`), `title`, `notes[]`, `type`. The top `relatedVersion` is the current ERepo version — ideal cheap version check.

## Update / freshness strategy (per domain)
- **ERepo**: poll `news` (18 KB) → compare top `relatedVersion`. If changed, refresh summary + invalidate variant caches.
- **Gene Validity / Gene Dosage**: no HTTP caching headers; cheap full pulls. Store `{total, max_record_date, sha256(canonical_rows)}`. Refresh detects change by re-pull + compare (downloads are 1.5–1.8 MB, ~3s).
- **Actionability**: store `{count, max(metadata.lastUpdated), sha256(brief)}`; re-pull brief (484 KB) to detect change.
- Global manifest records per-domain `{source_url, fetched_at, signal, hash, record_count}` so the MCP can report data provenance/age in `get_server_capabilities` and per-tool `_meta`.

## Implications for design
- Three of four domains are small enough to ship as a **prebuilt local snapshot** (SQLite) refreshed by an ETL CLI → fast, token-efficient, offline-capable tools.
- ERepo variant-level detail is large/unbounded → **hybrid**: ship per-gene summary counts locally; fetch variant interpretations **live** by gene/CAID/HGVS on demand (with short-TTL cache), keyed to the `news` version.
- All four have clean per-record provenance (perm_id / CAID / docId) for a strong citation contract.
