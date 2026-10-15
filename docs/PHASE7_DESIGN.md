# Phase 7 design — the Spark + Delta Lake lakehouse

**Scope of this document:** the decisions made at Phase 7 kickoff (2026-10-13 to 2026-10-15) about
how the SEC data becomes a clean silver ledger and a gold layer the agent can query, and the real
measurements behind each decision. `PHASES.md` holds the phase-by-phase plan;
`docs/PHASE6_DESIGN.md` holds the platform decisions D1-D11 and the ingestion design. This
document continues that numbering (D12 onward) and does not repeat them.

**Status:** decisions D12-D24 locked 2026-10-15 from real-data exploration of the bronze Parquet
(2026q2, plus 2015q1 for the tag-change checks). Open items are listed in section 11.
**Last updated:** 2026-10-15.

---

## 1. The dataset in plain language

Every company listed in the US must publish its financial results with the SEC, the US securities
regulator. Since 2009 the numbers inside those reports have been published in a machine-readable
format. Each quarter the SEC releases everything filed in that quarter as four tables:

- **Filings (`sub`).** One row per report: who filed it, what kind of report it was, when it was
  filed, and which period it covers. The kinds you will see most:
  - **10-K:** a US company's annual report. It covers the whole fiscal year.
  - **10-Q:** a US company's quarterly report. Filed for the first three quarters only.
  - **20-F:** a foreign company's annual report (Infosys, for example). The counterpart of a 10-K.
  - **11-K:** the annual report of an employee savings or stock plan. Not a business.
  - **S-1:** the document a company files before going public. Contains financial statements.
  - A trailing **`/A`** (for example `10-K/A`) marks an amendment that corrects an earlier filing.
- **Numbers (`num`).** One row per figure in a report, for example "Microsoft, revenue,
  January to March 2026, $82.9 billion". This is the big table (about 3.6 million rows in a recent
  quarter).
- **Tags (`tag`).** A dictionary: what each figure's name (its "tag") means.
- **Presentation (`pre`).** How each figure appeared on the printed statement: which statement,
  which line, and the label the company used.

Only the numbers from the main financial statements are included: the balance sheet (a snapshot
on one date: what the company owns and owes), the income statement (performance over a period:
revenue minus costs gives profit) and the cash flow statement (cash moving in and out over a
period), plus the equity and comprehensive-income statements. The data has no stock prices, no
news and none of the written text of the reports.

### What a row means (with a made-up company)

"Acme Corp" files its annual report for the year ending 31 December 2025. The data holds one row
in `sub` for that filing, and many rows in `num`, each a number with an exact date range:

- Revenue, ending 2025-12-31, covering 4 quarters, in USD: 900,000,000
- Revenue, ending 2024-12-31, covering 4 quarters, in USD: 850,000,000 (last year, shown for
  comparison)
- Assets, on 2025-12-31 (a single date), in USD: 2,000,000,000

A report always contains the current period **and last period's figures for comparison**, so every
number appears in at least two reports. If the company corrected last year's figure in the newer
report, the two reports disagree. That is a restatement.

### Why the data cannot be used as published

1. **The same figure appears in many reports**, sometimes with different values (restatements,
   amendments).
2. **The same idea has several names.** Revenue has at least four different tag names, and the
   names changed around 2018.
3. **A total sits next to its breakdowns** in the same table (Microsoft's revenue for one quarter
   has 14 rows: the total, and slices by business unit, by region and by product).
4. **Periods come in different shapes:** a single date, a quarter, a year, and year-to-date totals.
5. **Nobody files a Q4 report.** The annual report covers the whole year, so the fourth quarter has
   to be calculated.
6. **Many currencies and units:** US dollars, euros, yen, and non-money units such as share counts.
7. **Every company's fiscal year ends in a different month.** Microsoft's ends in June, so its
   January to March quarter is its third.
8. **Company names change** while the company stays the same.
9. **Parent versus total:** a company that owns part of a subsidiary reports both a profit that
   includes the minority owners' share and a smaller profit that belongs to its own shareholders.
10. **The SEC says so itself:** the data is "as filed" and "may contain redundancies,
    inconsistencies, and discrepancies".

### What we build

A pipeline that turns this into **one trustworthy figure per company, metric and period**, plus
ready-made tables for questions such as "how did this company's revenue grow?" and "which sector
did best last year?". The agent from Phases 1-5 will later answer those questions in plain
English.

### The questions version 1 answers

1. The total revenue (or another metric) of a company between two years.
2. A line chart of a metric for a company across years.
3. How a company is doing in recent quarters, and the short-term trend of revenue and profit.
4. Key metrics for a company listed for a period or its full history.
5. A company compared with another in the same or a similar industry.
6. Companies grouped by industry (IT, petrochemicals, banking and so on).
7. How companies in each industry are performing.
8. Which industry is doing best now, or did best in earlier years.

**Stretch (Phase 7 if time allows):** has a company ever amended or restated a filing, and what did
its revenue and profit look like before and after (needs a history of replaced values).
**Deferred:** how much each business segment or co-registrant contributes to the parent, and
anything involving stock prices. The data holds no penalties or legal information, so "did a
refiling lead to penalties" cannot be answered; we can only show that an amendment happened and
that numbers changed.

---

## 2. The layers in plain language

- **Bronze** (done in Phase 6): an exact copy of what the SEC published, as Parquet files, one
  folder per quarter.
- **Silver:** the clean ledger. One row per company, metric and period, always holding the latest
  reported value, with a pointer back to the filing it came from. A second silver table holds one
  row per company per name, so name changes are remembered.
- **Gold:** ready-made answers. A star schema (one central table of figures surrounded by company,
  metric and period lookup tables), summary tables per company per year and per quarter with growth
  rates and margins, and a summary per industry. Gold is copied into BigQuery for the agent.

---

## 3. Real-data findings

Everything in this section was measured on the bronze Parquet in `data/lake/`, not taken from the
SEC's readme. Counts are for **2026q2** unless a section says otherwise.

### 3.1 Which rulebook names each figure (`num.version`)

`version` says where a tag's name comes from. A standard tag shows its rulebook and year
(`us-gaap/2025`); a company-invented ("custom") tag shows the filing's own accession number.

| `version` | Rows | Share |
|---|---|---|
| `us-gaap/2025` | 2,458,911 | 68.1% |
| `us-gaap/2026` | 534,061 | 14.8% |
| `ifrs/2025` | 187,102 | 5.2% |
| `us-gaap/2024` | 47,611 | 1.3% |
| `us-gaap-ebp/2026` | 34,100 | 0.9% |
| `ifrs/2024` | 23,610 | 0.7% |
| `us-gaap-ebp/2025` | 15,121 | 0.4% |

- 7,128 distinct `version` values in total; the other 7,100 or so are custom tags, one value per
  filing that defines any. The seven rows above sum to about 91.5% of `num`, so custom tags are at
  most about 8.5% (a ceiling: a few rarer standard versions may sit below the top 12 I looked at).
- **The IFRS prefix is `ifrs`, not `ifrs-full`.** The Phase 6 handoff guessed wrong.
- `us-gaap-ebp` is a separate rulebook for employee benefit plans (11-K filings). Left out of the
  first concept map: a benefit plan has no revenue or gross profit.
- The taxonomy **year** varies inside one quarter (2024, 2025, 2026), and in 2015q1 it was 2013,
  2014 and 2015. So any key must keep the rulebook family and drop the year.

### 3.2 Units (`num.uom`)

- All rows: USD 2,805,729 (about 78%); `pure` 294,193 (ratios); `shares` 235,740; then CNY 76,307,
  BRL 27,281, EUR 26,421, KRW 24,694, CAD 23,635, JPY 11,938, MXN 11,815. 107 distinct units.
- IFRS rows only: 47 distinct units. USD 66,230 (about 31%), KRW 21,955, BRL 21,491, EUR 20,292,
  CAD 12,583, MXN 10,161, CNY 7,656, ARS 7,500, CLP 6,527, JPY 5,966.
- **Currency is independent of rulebook.** CNY has 76,307 rows overall but only 7,656 are IFRS, so
  about 69,000 CNY rows come from filers using `us-gaap`. Many IFRS filers report in USD.
- `pure` and `shares` are not money.

### 3.3 Whole company versus a part (`segments`, `coreg`)

- `segments` is NULL on 1,418,876 rows (39.3%). `coreg` is NULL on 3,547,589 rows (98.3%). Rows
  where both are NULL: 1,418,876, the same as the `segments` count. **Every row with no segment
  also has no co-registrant.** These are the whole-company figures.
- The other 60.7% of rows carry a `segments` value: text made of `Axis=Member;` pairs.
- Microsoft's 10-Q (`0001193125-26-191507`), revenue for the quarter ending 2026-03-31: 14 rows
  for one metric, one date, one period length and one currency.
  - Whole company (`segments` empty): 82,886,000,000.
  - By business segment: Intelligent Cloud 34,681M + More Personal Computing 13,192M +
    Productivity and Business Processes 35,013M = 82,886M (adds up to the total).
  - By geography: US 42,336M + non-US 40,550M = 82,886M (adds up).
  - By product or service: Gaming, LinkedIn, Dynamics and so on; these overlap (some are
    subtotals) and do not sum cleanly.
- `coreg` is filled on about 1.7% of rows (about 61,000). The SEC defines it as a co-registrant,
  the parent, or another entity such as a guarantor. **Not yet examined on real rows.** It is
  probably not the same as "a subsidiary".
- Segment and geography revenue is already in the data we ingested, for at least this company.
  This contradicts the `PHASES.md` note that FSDS "can't answer" segment questions; how common it
  is across companies is unmeasured.

### 3.4 Period length (`num.qtrs`)

| `qtrs` | Rows | Meaning |
|---|---|---|
| 0 | 1,980,167 | a single date (balance sheet) |
| 1 | 974,152 | one quarter |
| 4 | 515,012 | a full year |
| 2 | 76,435 | six months to date |
| 3 | 62,582 | nine months to date |
| others | a few hundred | 5 to 113: odd durations, probably transition periods or cumulative since inception (guess) |

- Confirmed on Microsoft's 10-Q: `qtrs=3` revenue is 241.8B against 82.9B for `qtrs=1`, and net
  income 98.0B against 31.8B, consistent with nine months versus one quarter.
- A 10-Q's income-statement figures come in two shapes for the same date (the quarter and the year
  to date), for both the current year and the prior year: four revenue figures in one filing.
- Balance-sheet figures in a 10-Q compare against the **last fiscal year end**, not the same date a
  year earlier (Microsoft's assets: 2026-03-31 against 2025-06-30).
- Cash flow also had a direct `qtrs=1` figure for Microsoft (operating cash flow 46.7B for the
  quarter). A year-to-date-only cash flow was expected and **did not appear** for this company;
  other filers are unmeasured, so silver must handle both.

### 3.5 There is no Q4, and the quarter folder is not the fiscal quarter

Filings in 2026q2 by form and fiscal period (`fp`), top groups: 10-Q Q1 4,635; 11-K (none) 651;
10-K FY 570; 20-F FY 521; 10-Q Q3 352; 10-Q Q2 333; 11-K FY 80; 10-K/A FY 72; S-1 (none) 63;
POS AM (none) 57; S-1/A (none) 54; 6-K Q2 49. No Q4 filing appears.

- The `2026q2` file holds filings **submitted** April to June, so most of it describes fiscal
  Q1. The `quarter=` folder name is the filing window, not the fiscal period.
- The `quarter` value is **not a column inside the Parquet files.** It exists only in the folder
  name (`quarter=2026q2/`). Spark reads it as a column when it reads the parent folder (Hive-style
  partitioning); reading a single file does not give it.
- `fy` and `fp` are the filer's own labels and are empty on 11-Ks.
- Microsoft's 10-Q row: `fye` `0630`, `fy` 2026, `fp` Q3, `period` 2026-03-31. Our derived label
  (fiscal Q3 of FY2026) matches the filer's.

### 3.6 Revenue: the names changed, and totals sit next to parts

- **2015q1** (filings using each tag, by taxonomy year, summed): `Revenues` about 3,000;
  `SalesRevenueNet` about 1,870; `SalesRevenueGoodsNet` about 1,000; `SalesRevenueServicesNet`
  about 650. No `RevenueFromContract...` tag at all.
- **2026q2:** `RevenueFromContractWithCustomerExcludingAssessedTax` about 2,565; `Revenues` about
  2,240; `RevenueFromContractWithCustomerIncludingAssessedTax` about 490; IFRS `Revenue` about 204
  (`ifrs/2025`, `ifrs/2024`). `SalesRevenueNet` has disappeared. The usual reason is a 2018
  accounting-standard change (background knowledge, not verified in the data).
- `Revenues` is the only tag common to both eras.
- One custom `Revenue` tag exists (`version` equals an accession number). **Matching on the tag
  name alone would mistake it for the IFRS tag.** The concept map must match on rulebook plus tag.
- 5,058 us-gaap filings have at least one of the three modern revenue tags: 4,823 use exactly one,
  235 use two. Counting by (filing, end date, `qtrs`, unit): 12,269 groups; 552 have two revenue
  tags for the same period; **123 agree and 415 differ** (the other 14 are probably empty values).
- **UDR, Inc.** (`0000074208-26-000049`), quarter ending 2026-03-31: `Revenues` is 425,849,000,
  labelled "Total revenues" (income statement line 5); `RevenueFromContract...` is 2,528,000,
  labelled "Joint venture management and other fees" (line 3). A rule of "prefer the newer contract
  tag" would report UDR's revenue about 170 times too low.
- Comparing `Revenues` with the contract tags (529 periods with both): `Revenues` larger in 360
  (68%), equal in 110 (21%), smaller in 38 (7%); the other 21 are probably empty. The 38
  exceptions are not one pattern: one has `Revenues` of -594,349 against 1,750 (probably a
  fund-like entity); one has a steady gap of about 27.5M in both years (cause unchecked, possibly
  the including-tax variant).
- Industry-specific top lines exist where "revenue" is not the plain tag: banks
  (`InterestAndFeeIncomeLoansAndLeases`), insurers (`PremiumsEarnedNet`), property companies
  (`OperatingLeaseLeaseIncome`).

### 3.7 Profit and equity: parent versus total

- `NetIncomeLoss` is the profit attributable to the company's own shareholders; `ProfitLoss` is the
  total including minority owners. The "Loss" in the name means the value is signed: positive is a
  profit, negative a loss (Tecogen's `NetIncomeLoss` is -659,922 and -2,120,573).
- In 2026q2, 4,268 quarterly periods carry both tags: 2,100 equal, 2,167 differ. Examples:
  Diamondback Energy 2026-03-31: 25,000,000 against 144,000,000 (the gap is the minority owners'
  share); Voya Financial 2025-03-31: 156,000,000 against 151,000,000 (the total is **smaller**, so
  the gap can go either way); Tecogen differs by about 600 dollars.
- Equity has the same split: `StockholdersEquity` (parent) and
  `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` (total).
- IFRS parent-only tags exist but are less common: of 292 IFRS filings with a whole-company figure,
  `ProfitLoss` 288, `ProfitLossAttributableToOwnersOfParent` 167, `Equity` 288,
  `EquityAttributableToOwnersOfParent` 171. Expectation, not checked: a company with no minority
  owners does not report a separate parent-only line because it would equal the total.

### 3.8 Other metrics: coverage by tag (filings using the tag, whole-company rows)

| Tag | 2015q1 us-gaap | 2026q2 us-gaap | 2026q2 ifrs |
|---|---|---|---|
| `GrossProfit` | 2,561 | 2,620 | 159 |
| `OperatingIncomeLoss` | 4,929 | 5,213 | - |
| `ProfitLossFromOperatingActivities` | - | - | 199 |
| `NetIncomeLoss` | 6,262 | 6,284 | - |
| `ProfitLoss` | 2,252 | 3,092 | 288 |
| `Assets` | 6,852 | 6,632 | 287 |
| `Liabilities` | 5,203 | 5,983 | 247 |
| `StockholdersEquity` | 6,243 | 6,182 | - |
| `Equity` | - | - | 288 |
| `NetCashProvidedByUsedInOperatingActivities` | 4,200 | 6,495 | - |
| `CashFlowsFromUsedInOperatingActivities` | - | - | 275 |

Unlike revenue, these tags barely changed between 2015 and 2026. IFRS needs its own tag names for
most metrics; `Assets` and `Liabilities` keep the same names under both rulebooks.

### 3.9 Country and name

- `countryba` is NULL for 268 of 521 20-F filings and 582 of 7,714 filings overall; Infosys and
  Wipro have both `countryba` and `countryinc` NULL. A filter on `countryba = 'IN'` finds nothing.
  Country enrichment belongs in silver (see D23).
- `sub.name` is the company's name **at filing time**, which is the source of name history.

### 3.10 Versions checked against the sources (2026-10-13)

- `pyspark` 4.2.0 on PyPI; requires Python 3.10 or newer (CI's 3.11 and 3.13 are fine).
- Spark 4.2.0's documentation: runs on Java 17, 21 or 25.
- `delta-spark` 4.4.1 on PyPI; its GitHub release notes (5 October) say it is built for Spark
  4.2.0, 4.1.0 and 4.0.1. Delta's own compatibility page lists only 4.3.x for Spark 4.0 and 4.1,
  so that page is out of date. The pair will be proven by a smoke test (write a Delta table, read
  it back) before anything else is built. Spark 4.1 with the same Delta release is the fallback.

---

## 4. Decisions

### D12 — The package is `lakehouse/`, with its own requirements

All Spark code lives in a new top-level package `lakehouse/` with its own
`requirements-spark.txt`. Neither the app image nor the ingestion container gets Java, and the
~300+ MB PySpark install stays out of both. Nothing is imported from `src/` or `ingestion/`.

### D13 — CI gets a separate Spark job

Spark tests live in `tests/spark/` and run in their own CI job (OpenJDK 17, one Python version,
its own 70% coverage gate on `lakehouse/`). The existing `test` job gains
`--ignore=tests/spark` so it does not try to import `pyspark`. Cost: a separate job to maintain.
Benefit: app-only pushes stay fast.

### D14 — Spark runs in a WSL2 virtualenv on the personal laptop

Spark on native Windows needs Hadoop's `winutils` helper to write local files, and the Docker
image belongs to Phase 9. The repo is cloned inside WSL's own filesystem, not under `/mnt/c`, since
Spark touches many small files and the Windows-mounted drive is much slower. Java is an OpenJDK 17
build. Company approval of any specific OpenJDK build is unknown and is not claimed.

### D15 — The silver key

One silver row per **(`cik`, `metric`, `period_end`, `qtrs`, `uom`)**. `cik` identifies the company
(names change); `metric` replaces the tag; `period_end` is `ddate`; `qtrs` is the period length;
`uom` is the unit. The filing's `adsh` is **lineage, not part of the key**: it says where the value
came from.

### D16 — Which rows silver keeps

- Whole-company rows only: `segments` and `coreg` both NULL (section 3.3).
- Standard tags only: `version` starts with `us-gaap/` or `ifrs/`. This drops custom tags and the
  `us-gaap-ebp` family.
- Forms 10-K, 10-Q, 20-F, 40-F and their `/A` amendments (amendments carry restatements). 6-K, 11-K
  and S-1 are out of version 1.
- `qtrs` 0, 1 and 4 only. The 2 and 3 figures are used only as ingredients to derive quarters.
- Monetary units for the money metrics; `shares` and `pure` are not mapped in version 1.

### D17 — Latest filed wins

When the same key appears in several filings, keep the one with the newest `filed` date, then the
newest `accepted` timestamp, then the largest `adsh` as a final deterministic tie-break. The key
excludes `version` and `adsh`, because both change between filings.

### D18 — The concept map and the eight metrics

A small seed file maps (rulebook, tag) to a metric, with a priority and a rule type. Tags were
chosen from the counts in section 3, not from memory. Version 1 has eight metrics: `revenue`,
`gross_profit`, `operating_income`, `net_income`, `total_assets`, `total_liabilities`,
`shareholders_equity`, `operating_cash_flow`. Matching is on **rulebook plus tag name**, never the
tag name alone (section 3.6). More metrics later means more seed rows and no code change.
Banks, insurers and funds have no plain revenue tag; version 1 leaves them unmapped for revenue,
and coverage is measured and documented rather than hidden.

### D19 — Conflict rules are per metric

- **Revenue:** when a filing has two candidate tags for the same period and unit, take the larger
  value and set `resolved_from_conflict`. The larger one is the total whenever one tag is a part of
  the other (UDR). Revisit in Spark across all quarters, including the 38 exceptions.
- **Net income and shareholders' equity:** a priority list, **parent-only first**
  (`NetIncomeLoss`, `StockholdersEquity`; IFRS `ProfitLossAttributableToOwnersOfParent`,
  `EquityAttributableToOwnersOfParent`), then the IFRS total tag as a **flagged fallback**. "Take
  the larger" would be wrong here, since the total is usually larger. Including-minority metrics
  are deferred.
- A negative revenue is flagged for review, not silently trusted.

### D20 — Currency is kept, never converted

Every silver row keeps its `uom`. We never add across currencies and do no exchange-rate
conversion. Comparisons across companies are fair only within one currency, mostly USD.

### D21 — Periods: exact dates first, fiscal labels derived

Every row has an exact range: `period_end = ddate`, and the start is `qtrs x 3` months earlier
(empty for a single date). Fiscal labels are derived from `fye` (month and day of the fiscal year
end, from `sub`) and `ddate`:

```
months_into_year = (month of ddate - month of fye) modulo 12      (0 becomes 12)
fiscal_quarter   = months_into_year / 3
fiscal_year      = the calendar year in which the fiscal year ends
```

Microsoft: `fye` 0630, `ddate` 2026-03-31 gives 9 months in, fiscal Q3 of FY2026, calendar Q1 2026.
The fiscal-year number is a naming convention (a company with a January year end may call the year
ending January 2027 "fiscal 2026"); we apply one rule to everyone and compare it with the filer's
own `fy`/`fp` as a check. The calendar year and quarter are stored too, for comparing companies.
Calendarising off-cycle fiscal years is an approximation (a January year end puts quarter ends in
April, July, October and January) and is documented as such in `docs/DATA_MODEL.md`. The SEC rounds
`ddate` to a month end, so 52/53-week years still land on clean keys.

### D22 — Derived values: Q4 and year-to-date subtraction

- **Q4 = full year (`qtrs=4`) minus nine months to date (`qtrs=3`)** from the Q3 filing, or the
  full year minus the three quarters.
- **A quarter missing as a direct `qtrs=1` figure** (possible for cash flow) is derived by
  subtracting year-to-date figures.
- Every such row has `is_derived = true`. Build order: annual figures first, then Q1 to Q3, then Q4.

### D23 — The company table, name history and country

`silver.company` has one row per company per name period (slowly changing dimension type 2):
`cik`, `name`, `sic`, country, `valid_from`, `valid_to`. Country is enriched where the SEC left it
NULL (section 3.9), using `countryinc`, state codes and other `sub` columns; the exact rule is
decided when the real NULL patterns are examined.

### D24 — Gold version 1

- Dimensions: `dim_company`, `dim_metric`, `dim_period`.
- Fact: `fct_financials` (company, metric, period, value, currency).
- Summaries for the agent: one row per company per fiscal year, and one per company per quarter,
  with year-on-year and quarter-on-quarter growth, trailing four quarters and margins.
- Sector summary: from a mapping seed that groups SIC codes into about a dozen sectors, built from
  the SEC's published SIC list (a design choice, partly subjective). Banks and insurers will
  compare on net income, assets and equity, not revenue.
- Gold is exported to Parquet and batch-loaded into BigQuery, partitioned by period and clustered
  by company.

---

## 5. Silver and gold shape

### `silver.financials`

| Column | Meaning |
|---|---|
| `cik` | company ID |
| `metric` | one of the eight standard names |
| `uom` | currency or unit |
| `period_start` | start date (empty for a single date) |
| `period_end` | end date |
| `qtrs` | period length: 0, 1 or 4 |
| `fiscal_year`, `fiscal_quarter` | derived fiscal labels (quarter empty for annual rows) |
| `calendar_year`, `calendar_quarter` | by the end date |
| `value` | the reported value, exact decimal |
| `source_adsh`, `form`, `filed`, `accepted` | lineage: which filing, when |
| `source_tag`, `taxonomy` | which tag and rulebook won |
| `is_derived` | calculated (Q4 or a subtracted quarter), not reported |
| `resolved_from_conflict` | more than one candidate tag existed for this period |

Example rows (Microsoft, real values from section 3):

```text
cik     metric         period_end  qtrs  fiscal     cal      uom  value            source_tag
789019  revenue        2026-03-31  1     FY2026 Q3  2026 Q1  USD  82,886,000,000   RevenueFromContract...
789019  revenue        2025-03-31  1     FY2025 Q3  2025 Q1  USD  70,066,000,000   RevenueFromContract...
789019  net_income     2026-03-31  1     FY2026 Q3  2026 Q1  USD  31,778,000,000   NetIncomeLoss
789019  total_assets   2026-03-31  0     FY2026 Q3  2026 Q1  USD  694,228,000,000  Assets
```

**Size estimate (not measured):** for one company and one fiscal year, five flow metrics at five
rows each (four quarters and one annual) plus three balance-sheet metrics at four rows each is
about 37 rows. A company with 15 years of history has about 550 rows. If about 10,000 companies
average eight years, silver is on the order of 3 million rows, from roughly 200 million bronze
figures. To be replaced by the real count after the full run.

### Gold

Described in D24; table definitions go in `docs/DATA_MODEL.md` once built.

---

## 6. Spark concepts this phase teaches

The owner is new to Spark; each concept is taught just before the step that uses it.

- A DataFrame, lazy evaluation, transformations versus actions.
- Partitions, shuffles, and why joins and regrouping are expensive.
- Broadcast joins: `sub` (about half a million filings over the full history) is small enough to
  copy to every worker instead of reshuffling about 200 million figures.
- Window functions (latest filed per key), and how they differ from `GROUP BY`.
- Delta Lake: the transaction log (`_delta_log`), MERGE, time travel, schema enforcement,
  `OPTIMIZE` and `VACUUM`.
- Null-safe equality: SQL's `NULL = NULL` is not true, and about 39% of `num` rows have a NULL
  `segments`, so MERGE needs null-safe matching (or a sentinel value) on `segments` and `coreg`.
- Testing Spark code with `pyspark.testing.assertDataFrameEqual`.

---

## 7. Steps

**Weekend 1: setup and silver on one quarter**
1. This design document.
2. Personal laptop: check cores, free disk and WSL2; install OpenJDK 17; clone the repo inside WSL;
   venv; `requirements-spark.txt`; `.env`; rebuild 2026q2 and 2015q1 bronze.
3. Spark smoke test: start Spark with Delta, read the bronze folder, write and read a Delta table,
   inspect `_delta_log`.
4. `lakehouse/` skeleton, `tests/spark/conftest.py`, the CI `spark` job, `--ignore=tests/spark`.
5. The concept-map seed.
6. Silver facts job on one quarter, with a unit test per transformation.
7. `silver.company`.

**Weekend 2: full history, gold, BigQuery**
8. SEC request throttle (risk #29), then backfill all ~70 quarters of bronze.
9. Silver on the full history, then the incremental MERGE path, with a test that a full rebuild and
   an incremental run give identical results.
10. Derived Q4 and year-to-date subtraction.
11. Gold, including the SIC-to-sector seed.
12. BigQuery: confirm the $1 budget alert, a daily query quota, partition and cluster, export,
    batch load, test queries with `maximum_bytes_billed`.
13. `docs/DATA_MODEL.md`, status updates, doc corrections, PR.

**Weekend 3 (slack):** whatever overran, then the stretch goals (the restatement log and a DuckDB
versus Spark benchmark). Cut order if time runs short: benchmark first, then restatement log.

---

## 8. Cost

Spark, Delta Lake and the CI job are free (public repo). BigQuery is the only place money is
possible: before the first load, confirm the $1 budget alert, set a custom daily quota, partition
gold by period and cluster by company, and set `maximum_bytes_billed` on every query. Batch loads
are free; whether gold loads straight from local files or needs a GCS staging copy is checked at
step 12 (GCS's 5 GB free tier covers a staging copy).

---

## 9. Risks

- **Overrun (risk #25):** learning Spark and the SEC format together. Weekend 3 is slack.
- **Memory and disk:** Spark's driver defaults to 1 GB, far too little for regrouping about 200
  million rows; set it explicitly before the backfill. The personal laptop has 16 GB of RAM; cores
  and free disk are to be checked (the backfill needs roughly 15 GB or more including ZIPs, bronze,
  Spark's temporary files, silver and gold).
- **SEC throttle (risk #29):** add the request delay before any backfill.
- **Tag-rule errors that look plausible:** the revenue and net income rules are only as good as the
  samples behind them. The conflict flags exist so every uncertain row can be counted and reviewed.
- **BigQuery cost** (section 8).

---

## 10. Interview talking points

- **"How did you handle restatements?"** "Every filing repeats last year's figures, so the same
  fact arrives in several filings, sometimes corrected. Silver keeps the latest filed value per
  company, metric and period, with a pointer to the filing, and I verified the approach on real
  data: 552 periods in one quarter had two revenue tags and 415 of them disagreed."
- **"Tell me about a data-quality trap you found."** "Revenue has more than one tag name. Taking the
  newer contract-revenue tag would have reported UDR's revenue at 2.5 million instead of 425.8
  million. I made the rule per metric, took the larger value for revenue, and flagged every
  resolved conflict so it can be audited."
- **"Why is the key not just the tag?"** "Tag names changed in 2018, differ between US and
  international rulebooks, and a company can invent its own with the same name as a standard one.
  The key is company, metric, period, period length and currency."
- **"How do you compare companies with different fiscal years?"** "Every figure carries an exact
  date range. Fiscal labels are derived from the company's fiscal year end, and a calendar view is
  stored beside it for cross-company comparison."
- **"Parent versus total profit?"** "Diamondback's shareholders earned 25 million in a quarter in
  which total profit was 144 million; the rest belonged to minority owners. I standardised on the
  parent figure and kept the rule explicit."
- **"Why Spark, why Delta?"** See `docs/PHASE6_DESIGN.md` D2 and D4; this phase adds the evidence
  (a regrouping across all history, MERGE for restatements, time travel for what we believed before
  a correction).

---

## 11. Open items

- Whether 6-K filings (foreign interim reports) join version 1 (recommended: no).
- The exact SIC-to-sector grouping (step 11).
- Whether the restatement log moves into version 1.
- `coreg` rows: look at real examples before deciding whether any co-registrant data is useful.
- A segment fact table as a later extension, and correcting the `PHASES.md` statement that FSDS
  cannot answer segment questions.
- Cash flow: how often is a direct `qtrs=1` figure missing (measured in Spark across all quarters).
- The 38 revenue exceptions and the 21 unexplained comparisons (probably empty values).
- Country-enrichment rule for NULL `countryba`/`countryinc`.
- Coverage per metric: the share of 10-K and 10-Q filers that get a value for each of the eight.
