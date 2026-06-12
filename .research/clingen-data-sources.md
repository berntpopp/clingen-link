# ClinGen Data Sources — Research for `clingen-link` MCP Server

Research date: **2026-06-12**. All URLs verified live by direct fetch unless noted.
Maintainer: bernt.popp@charite.de

---

## 0. Central hubs & meta

| Resource | URL |
|---|---|
| **Downloads hub (THE key page)** | https://search.clinicalgenome.org/kb/downloads |
| Per-domain anchors | `…/kb/downloads#section_gene-disease-validity`, `#section_dosage` |
| Main org site | https://clinicalgenome.org |
| Curation interface (GCI) | https://curation.clinicalgenome.org |
| Search/knowledge base | https://search.clinicalgenome.org/kb |
| Dosage map | https://dosage.clinicalgenome.org/ |
| Actionability | https://actionability.clinicalgenome.org/ac/ |
| Evidence Repository (ERepo) | https://erepo.clinicalgenome.org/evrepo/ |
| Allele Registry (CAR) | https://reg.clinicalgenome.org/ (docs: https://reg.clinicalgenome.org/docs/cg-car/) |
| CSpec Registry (criteria) | https://cspec.clinicalgenome.org/cspec/srvc |
| Linked Data Hub (LDH) | https://ldh.clinicalgenome.org/ldh/srvc (docs: https://ldh.clinicalgenome.org/docs/ldh/api/) |

**No Swagger/OpenAPI spec was found** for any ClinGen service. APIs are documented prose-only (and several only via internal/GitLab wikis).

**Licensing:** ClinGen data is released under **Creative Commons Attribution 4.0 International (CC BY 4.0)**, © ClinGen (confirmed in the platforms paper, PMC12001867). Each download page carries the disclaimer "not intended for direct diagnostic use or medical decision-making without review by a genetics professional." Citation guidance is linked from the downloads page ("how to cite ClinGen"). Recommended primary citation for gene–disease validity: Strande et al. 2017, Am J Hum Genet (PMID 28552198); platform paper: Wright et al., *Annu Rev Biomed Data Sci* 2025 (PMC12001867).

---

## 1. Gene–Disease Validity

### 1.1 Bulk download (RECOMMENDED primary source)
- **URL:** `https://search.clinicalgenome.org/kb/gene-validity/download`
- **Format:** CSV. `Content-Disposition: attachment; filename=Clingen-Gene-Disease-Summary-2026-06-12.csv` (filename date = generation date).
- **Size:** ~1.1 MB (`Content-Length: 1101760`), ~3,000+ classifications.
- **Built in real-time** (page text: "This file is built in real-time"). Confirmed: `Last-Modified` header = **time of the request**, regenerated on every call.
- **File layout:** 4 banner/metadata lines, then a `+++` separator, a header row, another `+++` separator, then data. Parsers must skip the first 6 lines.
  - Line 1: `"CLINGEN GENE DISEASE VALIDITY CURATIONS",…`
  - Line 2: `"FILE CREATED: 2026-06-12",…`  ← **freshness marker (date only)**
  - Line 3: `"WEBPAGE: https://search.clinicalgenome.org/kb/gene-validity",…`
- **Column schema (10 columns):**
  1. `GENE SYMBOL`
  2. `GENE ID (HGNC)`  e.g. `HGNC:20`
  3. `DISEASE LABEL`
  4. `DISEASE ID (MONDO)`  e.g. `MONDO:0013212`
  5. `MOI`  (mode of inheritance, e.g. `AD`, `AR`)
  6. `SOP`  (e.g. `SOP10`)
  7. `CLASSIFICATION`  (`Definitive`, `Strong`, `Moderate`, `Limited`, `Disputed`, `Refuted`, `No Known Disease Relationship`)
  8. `ONLINE REPORT`  ← per-record permalink (see 1.2)
  9. `CLASSIFICATION DATE`  ISO8601 e.g. `2024-03-14T16:00:00.000Z`  ← **per-record freshness**
  10. `GCEP`  (Gene Curation Expert Panel name)

### 1.2 Per-record permalink (CGGV assertion ID)
- Format embedded in column 8:
  `https://search.clinicalgenome.org/kb/gene-validity/CGGV:assertion_<uuid>-<approval-timestamp>`
  e.g. `…/CGGV:assertion_92de3832-c272-4993-8586-288c6331dec2-2024-03-14T160000.000Z`
- The trailing timestamp encodes the approval datetime → permalink itself is version-stamped.
- **Returns HTML only.** Tested `Accept: application/json`, `/download`, `.json` suffix — all return HTML 200 or 404. There is **no public per-record SEPIO/JSON-LD** export from search.clinicalgenome.org.

### 1.3 GraphQL / SEPIO API (GeneGraph) — NOT publicly available
- GitHub: https://github.com/clingen-data-model/genegraph ("RDF triplestore of gene information using GraphQL APIs").
- Host `https://genegraph.clinicalgenome.org/` serves a JS SPA (`main.56.js`); **no public GraphQL endpoint found**. Probed `/graphql`, `/api/graphql`, `/api/graphql/`, `/r4/graphql`, `/resources/graphql`, `/api/r4` → all 404; `/api` → 502.
- The ClinGen platforms paper (PMC12001867, 2025) explicitly states the GraphQL API is **internal-only**: *"We currently have a version of this API in use internally, to be released to the public following internal validation and testing."*
- **Conclusion:** Do **not** build on GeneGraph GraphQL for v1. Use the CSV download (1.1). Revisit later.

### 1.4 Freshness strategy (gene-validity)
The download is regenerated each request, so HTTP `Last-Modified`/`ETag` are **useless** here.
- **Cheapest signal:** issue a `HEAD` and read the date in `Content-Disposition: filename=Clingen-Gene-Disease-Summary-YYYY-MM-DD.csv`. If unchanged vs. last seen, content date is unchanged. (Note: the date is "today", so it changes daily even if curations didn't — see caveat.)
- **Robust signal:** download, then compare the max of column 9 `CLASSIFICATION DATE` across rows, and/or a content hash of the data rows (excluding the `FILE CREATED` banner line which changes daily). New curations ⇒ a newer max `CLASSIFICATION DATE` or changed row hash.
- Recommended: store last row-count + max(CLASSIFICATION DATE) + sha256 of sorted data rows; re-pull daily, diff cheaply.

---

## 2. Gene Dosage (Haploinsufficiency / Triplosensitivity)

Two delivery channels: (a) FTP/HTTPS static files (daily, cacheable) — **RECOMMENDED**; (b) real-time web CSV.

### 2.1 FTP/HTTPS static files (RECOMMENDED — supports conditional GET)
Base: `https://ftp.clinicalgenome.org/` (also `ftp://`). HTTP works and is preferred.
README: `https://ftp.clinicalgenome.org/README` — "These files are updated daily… refreshed nightly and are available for the last 60 days."

| File | URL | Size | Last-Modified (observed) |
|---|---|---|---|
| Gene curation, GRCh37 | `https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh37.tsv` | ~240K | 2026-06-11 |
| Gene curation, GRCh38 | `https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv` | ~239K | 2026-06-11 |
| Region curation, GRCh37 | `https://ftp.clinicalgenome.org/ClinGen_region_curation_list_GRCh37.tsv` | — | — |
| Region curation, GRCh38 | `https://ftp.clinicalgenome.org/ClinGen_region_curation_list_GRCh38.tsv` | — | 2026-04-28 (in-file date) |
| Haploinsufficiency BED GRCh37 | `https://ftp.clinicalgenome.org/ClinGen_haploinsufficiency_gene_GRCh37.bed` | 48K | 2026-06-11 |
| Haploinsufficiency BED GRCh38 | `https://ftp.clinicalgenome.org/ClinGen_haploinsufficiency_gene_GRCh38.bed` | 48K | 2026-06-11 |
| Triplosensitivity BED GRCh37 | `https://ftp.clinicalgenome.org/ClinGen_triplosensitivity_gene_GRCh37.bed` | 40K | 2026-06-11 |
| Triplosensitivity BED GRCh38 | `https://ftp.clinicalgenome.org/ClinGen_triplosensitivity_gene_GRCh38.bed` | 40K | 2026-06-11 |
| Recurrent CNV BED hg37 (current = V2.1) | `https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg37.bed` | 16K | 2026-03-18 |
| Recurrent CNV BED hg38 (current = V2.1) | `https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg38.bed` | 15K | 2026-03-18 |
| Recurrent CNV BED V2.0 (legacy) | `…/ClinGen_recurrent_CNV_V2.0-hg37.bed`, `…-hg38.bed` | — | 2026-01-05 |
| MONDO titles helper | `https://ftp.clinicalgenome.org/mondo_titles.tsv` | 1.1M | — |
| **Dated daily archive** | `https://ftp.clinicalgenome.org/archive/<YYYYMMDD>/` | — | snapshots `20201201/` … `20260610/` |

> README NOTE: As of 2026-03-18, recurrent CNV files moved to **V2.1**; V2.0 retained short-term. Pin to V2.1.

**Gene curation TSV — header lines** (lines start with `#`; 5 comment lines then a `#`-prefixed column header line):
```
#ClinGen Gene Curation Results
#11 Jun,2026                                  ← freshness marker (date)
#Genomic Locations are reported on GRCh38 (hg38): GCF_000001405.36
#https://www.ncbi.nlm.nih.gov/projects/dbvar/clingen
#to create link: …clingen_gene.cgi?sym=Gene Symbol
```
**Gene curation TSV — 23 columns** (header row, tab-separated):
`Gene Symbol`, `Gene ID` (NCBI numeric), `cytoBand`, `Genomic Location` (e.g. `chr16:70252298-70289506`), `Haploinsufficiency Score`, `Haploinsufficiency Description`, `Haploinsufficiency PMID1..PMID6`, `Triplosensitivity Score`, `Triplosensitivity Description`, `Triplosensitivity PMID1..PMID6`, `Date Last Evaluated` (e.g. `2025-06-27`) ← **per-record freshness**, `Haploinsufficiency Disease ID` (MONDO), `Triplosensitivity Disease ID` (MONDO).

**Region curation TSV — columns** (analogous; first two differ): `ISCA ID`, `ISCA Region Name`, `cytoBand`, `Genomic Location`, then same HI/TS score/description/PMID/date/disease columns as genes.

**BED files** (`track name=…` first line, then 5 cols): `chrom`, `start`, `end`, `gene symbol`, `score`. Per README, text scores are numerically encoded: **`Dosage sensitivity unlikely → 40`**, **`Gene associated with autosomal recessive phenotype → 30`** (other scores 0/1/2/3 as-is). Separate files per HI vs TS (BED has only one score column).

### 2.2 Real-time web CSV (alternative; same banner-layout caveat as gene-validity)
- Genes only: `https://search.clinicalgenome.org/kb/gene-dosage/download` → `filename=Clingen-Dosage-Sensitivity-2026-06-12.csv`
- Genes + regions: `https://search.clinicalgenome.org/kb/gene-dosage/downloadall`
- "Built in real-time" ⇒ `Last-Modified` = request time (not a freshness signal).
- **Columns (6):** `GENE SYMBOL`, `HGNC ID` (e.g. `HGNC:18149`), `HAPLOINSUFFICIENCY` (text label), `TRIPLOSENSITIVITY` (text label), `ONLINE REPORT` (`https://search.clinicalgenome.org/kb/gene-dosage/HGNC:18149`), `DATE` (ISO8601). Same 6-line banner to skip.

### 2.3 Freshness strategy (dosage) — BEST of the four domains
- The FTP static files **support conditional GET**. Verified:
  - `If-None-Match: "<etag>"` → **HTTP 304 Not Modified** (ETag `"3bdef-653f55f06728d"`).
  - `If-Modified-Since: <last-modified>` → **HTTP 304 Not Modified**.
- **Recommended:** store the `ETag` (and/or `Last-Modified`) per file; send a conditional `HEAD`/`GET` daily. 304 ⇒ skip download. This is the cheapest, cleanest freshness mechanism in all of ClinGen.
- Secondary in-file signal: the `#DD Mon,YYYY` comment line and per-record `Date Last Evaluated`.
- The `archive/<YYYYMMDD>/` directories give immutable dated snapshots for reproducibility/version pinning.

---

## 3. Clinical Actionability

JSON + TSV REST API per protocol (Adult / Pediatric). Base: `https://actionability.clinicalgenome.org/ac/`

### 3.1 Endpoints (all GET; CORS `*`; `Content-Type: application/json` or tsv)
| Purpose | URL |
|---|---|
| Adult summary, nested JSON | `https://actionability.clinicalgenome.org/ac/Adult/api/summ` |
| Adult summary, flat JSON | `https://actionability.clinicalgenome.org/ac/Adult/api/summ?flavor=flat` |
| Adult overall scores TSV | `https://actionability.clinicalgenome.org/ac/Adult/api/summ?format=tsv` |
| Adult individual scores TSV | `https://actionability.clinicalgenome.org/ac/Adult/api/summ/scoring?format=tsv&excludeNotScored=true` |
| Adult all assertions TSV | `https://actionability.clinicalgenome.org/ac/Adult/api/summ/assertion?format=tsv&assertionType=all` |
| Adult consensus assertions TSV | `https://actionability.clinicalgenome.org/ac/Adult/api/summ/assertion?format=tsv` |
| Per-doc SEPIO | `https://actionability.clinicalgenome.org/ac/Adult/api/sepio/doc/<docId>` (e.g. `AC022`) |
| Pediatric variants | replace `Adult` → `Pediatric` in any of the above |

- **Size/counts:** Adult `summ` JSON ≈ 612 KB; Adult overall TSV ≈ **253 rows** (Pediatric similar). Each `docId` is `AC####` (e.g. `AC1084`).
- **Doc IRI:** `https://actionability.clinicalgenome.org/ac/api/doc/AC022`.

### 3.2 JSON structure (nested) — key fields
```
[ { "docId":"AC022",
    "iri":"…/ac/api/doc/AC022",
    "curationType":"Gene-Condition",
    "metadata": { "lastUpdated":"Thu, 04 Apr 2019 16:27:31 -0000", "lastAuthor":"…" },  ← per-record freshness
    "disease":"…",
    "context": { "Adult": {
        "@id":"…/api/sepio/doc/AC022",
        "release": { "number":"2.0.0", "date":"Thu, 04 Apr 2019 00:00:00 -0000" },     ← version + date
        "genes":[ {"gene":"SDHD","geneOmim":"602690","diseases":[{"omim":"168000"}]} ],
        "searchDates":[…], "status": {"overall":"Retracted"|"Released"|…},
        "outcomes":[ {"label","severity","likelihood","interventions":[{"label","natureOfIntervention","effectiveness","overall"}]} ] } } } ]
```
- **TSV overall columns (27):** `docId, topicIri, curationType, latestSearchDate, lastUpdated, lastAuthor, context, contextIri, release, releaseDate, geneOrVariant, geneOmim, disease, omim, status-overall, status-stg1, status-stg2, status-scoring, outcome, outcomeScoringGroup, intervention, interventionScoringGroup, severity, likelihood, natureOfIntervention, effectiveness, overall`.
- **TSV assertion columns** include: `…, mondo, suggestedAssertion, scorer, preliminaryAssertion, consensusAssertion, status-assertion, status-overall, …` with assertion values like `Strong Actionability` / `Assertion Pending`.

### 3.3 Freshness strategy (actionability)
- **No HTTP `Last-Modified`/`ETag`** on these dynamic endpoints (nginx, no conditional support observed).
- **Per-record signals (excellent):** every record carries `metadata.lastUpdated` (RFC1123 datetime) and `context.<protocol>.release.number` + `release.date`. To detect new data cheaply: pull the small `…/summ?format=tsv` (≈253 rows), and compare the set of `(docId, release, lastUpdated)` tuples against last snapshot. Any new/changed tuple ⇒ updated curation. This payload is small (~hundreds of KB) so polling is cheap even without conditional GET.

---

## 4. Variant Pathogenicity — Evidence Repository (ERepo)

FDA-recognized variant database. Base API: `https://erepo.clinicalgenome.org/evrepo/api/`
(mirror host: `https://erepo.genome.network/…`; record links in data use the `genome.network` host).
API docs referenced as `https://erepo.clinicalgenome.org/docs/cg-erepo/` (SPA — did not render via fetch).

### 4.1 Bulk downloads (RECOMMENDED primary source)
| Format | URL | Notes |
|---|---|---|
| TSV (all interpretations) | `https://erepo.clinicalgenome.org/evrepo/api/summary/classifications/download` | `filename=erepo-tabbed.tsv`, `application/octet-stream` |
| CSV (all) | `https://erepo.clinicalgenome.org/evrepo/api/summary/classifications/download?type=csv` | `filename=erepo-tabbed.csv` |
| TSV (alt path) | `https://erepo.clinicalgenome.org/evrepo/api/classifications/all?format=tabbed` | `filename=erepo.tabbed.txt` (same data; `format` only accepts `tabbed`) |
- **Record count:** ~**12,683** interpretations (12,684 lines incl. header), verified by line count.
- **Service info endpoint:** `https://erepo.clinicalgenome.org/evrepo/api/summary/srvc`

**TSV column schema (20 columns):**
`Variation`, `ClinVar Variation Id`, `Allele Registry Id` (CAID, e.g. `CA281951`), `HGVS Expressions` (comma-joined list), `HGNC Gene Symbol`, `Disease`, `Mondo Id`, `Mode of Inheritance`, `Assertion` (e.g. `Pathogenic`/`Likely Pathogenic`/…), `Applied Evidence Codes (Met)`, `Applied Evidence Codes (Not Met)`, `Summary of interpretation`, `PubMed Articles`, `Expert Panel`, `Guideline` (CSpec URL), `Approval Date`, `Published Date`, `Retracted` (`true`/`false`), `Evidence Repo Link` (per-record permalink), `Uuid`.

### 4.2 Search / JSON API (per-query, structured SEPIO-ish)
- **Paginated JSON search:** `https://erepo.clinicalgenome.org/evrepo/api/classifications?format=json&matchLimit=<n>` — returns `{"variantInterpretations":[ { evidenceLinks, hgvs[], variationId, guidelines:[{label, outcome{label,@id}, agents:[{label,outcome,evidenceCodes:[{label,status,@id}]}]}], … } ]}`. Supports filters seen in the UI: `gene`, `condition`, `hgvs`, `classification`, `expertPanel`, `caid`, `clinVarVariationId`, `affiliationId`, `matchMode=exact`.
  - UI counterpart e.g. `https://erepo.clinicalgenome.org/evrepo/ui/classifications?matchMode=exact&gene=RUNX1`
- **Per-record SEPIO JSON-LD:** `https://erepo.clinicalgenome.org/evrepo/api/interpretation/<uuid>?format=json` → HTTP 200 `application/json` (verified). UUID = `Uuid` column / permalink tail.
- **Per-record permalink (in bulk file):** `https://erepo.genome.network/evrepo/ui/classification/<uuid>` (HTML UI) and `…/ui/interpretation/<uuid>`.
- Version history page (HTML): `https://erepo.genome.network/evrepo/ui/summary/news/`.

### 4.3 Freshness strategy (ERepo)
- **No HTTP `Last-Modified`/`ETag`** on the dynamic download (`application/octet-stream`, generated on request). Conditional GET not supported.
- **Per-record signals (good):** `Approval Date` and `Published Date` columns (and `Retracted`). To detect updates without re-downloading the body: there's no count/version endpoint, so the practical approach is:
  1. Pull the TSV (~few MB), compute max(`Published Date`) and row count + a hash of `(Uuid, Approval Date, Retracted)` tuples; compare to last snapshot. New/changed ⇒ update.
  2. The TSV is the canonical bulk; the `news/` page documents release notes for humans.
- Related registries for enrichment: Allele Registry `https://reg.clinicalgenome.org/` (CAID resolution), CSpec `https://cspec.clinicalgenome.org/cspec/srvc` (criteria specs referenced in `Guideline`).

---

## 5. Recommended download strategy per domain (summary)

| Domain | Primary endpoint | Format | Freshness mechanism (cheapest) |
|---|---|---|---|
| **Gene–Disease Validity** | `https://search.clinicalgenome.org/kb/gene-validity/download` | CSV (skip 6 banner lines) | No HTTP caching (real-time). Use `Content-Disposition` date + max(`CLASSIFICATION DATE`) + hash of data rows. |
| **Gene Dosage** | FTP TSV/BED at `https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv` (+ GRCh37, BED, region) | TSV / BED | **Conditional GET works** — store `ETag`/`Last-Modified`, send `If-None-Match`/`If-Modified-Since`, expect **304**. Best signal of all four. |
| **Clinical Actionability** | `https://actionability.clinicalgenome.org/ac/{Adult,Pediatric}/api/summ` (+ `?format=tsv`, `/scoring`, `/assertion`) | JSON / TSV | No HTTP caching; small payload (~253 rows). Diff `(docId, release.number, metadata.lastUpdated)` tuples. |
| **Variant Pathogenicity (ERepo)** | `https://erepo.clinicalgenome.org/evrepo/api/summary/classifications/download` (TSV; `?type=csv`) | TSV / CSV; JSON via `/api/classifications?format=json`; per-record JSON-LD via `/api/interpretation/<uuid>?format=json` | No HTTP caching; diff row count + max(`Published Date`) + hash of `(Uuid, Approval Date, Retracted)`. |

### Cross-cutting notes
- **GeneGraph GraphQL is internal-only** (per PMC12001867, 2025) — do not depend on it for v1; CSV is the supported gene-validity path.
- **No OpenAPI/Swagger** anywhere; treat schemas above (captured from live data) as the contract.
- **Only the dosage FTP files support HTTP conditional requests / 304.** Everything else is regenerated per request, so freshness = in-payload dates/hashes.
- **Banner-line caveat (gene-validity & dosage web CSV):** the `FILE CREATED` / date banner changes daily even when curations don't; exclude it from any content hash, and compare per-record dates to avoid false "updated" positives.
- **License:** CC BY 4.0, © ClinGen. Attribute ClinGen + cite the relevant ClinGen framework paper; surface the "not for direct diagnostic use" disclaimer to MCP consumers.

---

## 6. Verified-fetch evidence log (2026-06-12)
- `gene-validity/download` → 200, `filename=Clingen-Gene-Disease-Summary-2026-06-12.csv`, 1,101,760 bytes; `Last-Modified` == request time on repeat (real-time). 10-col schema captured.
- `ClinGen_gene_curation_list_GRCh38.tsv` → 200, `ETag "3bdef-653f55f06728d"`, `Last-Modified Thu, 11 Jun 2026 07:33:06 GMT`; `If-None-Match`/`If-Modified-Since` → **304**. 23-col schema captured.
- BED HI GRCh38 → `track name=…`, 5 cols, numeric score encoding confirmed.
- `archive/` → daily dirs `20201201/` … `20260610/` (through prior day).
- `actionability/ac/Adult/api/summ` → 200 JSON 612,448 bytes; nested + flat + tsv confirmed; 253 overall rows; `metadata.lastUpdated` + `release` per record.
- `erepo …/summary/classifications/download` → 200 TSV `erepo-tabbed.tsv`, 12,684 lines (~12,683 records), 20-col schema captured; `?type=csv` → `erepo-tabbed.csv`. `/api/classifications?format=json&matchLimit=1` → JSON SEPIO structure. `/api/interpretation/<uuid>?format=json` → 200 JSON.
- `genegraph.clinicalgenome.org` GraphQL probes (`/graphql`, `/api/graphql`, etc.) → 404/502; SPA only.
