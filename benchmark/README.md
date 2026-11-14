# Benchmark

[questions.json](questions.json) holds 20 questions on the Land Law [Luật Đất đai] 2013 and 2024, written and split before any retrieval run.
[verification.csv](verification.csv) is the output of `python -m src.benchmark` (run from `starter/`), which checks every gold quote against its page.

## Fields

Each question has the fields from [BENCHMARK.md](../BENCHMARK.md): `id`, `split`, `type`, `question`, `expected_answer`, `gold_source_id`, `gold_page`, `evidence_quote` and `filter`.
`gold_page` is the physical PDF page within the file named by `gold_source_id`, as listed in [data/manifest.json](../data/manifest.json).
`alternative_gold` lists every other page that answers the question equally well, each with its own quote; a hit on any of them counts.
`expected_answer` cites the article [Điều] and clause [khoản] the quote sits in, and the verifier prints the same location for cross-checking.

## Question mix

| Type | Count | What it tests |
|---|---:|---|
| `truc_tiep` (direct) | 9 | The question uses the law's own wording. |
| `dien_dat_lai` (paraphrase) | 5 | Everyday wording, for example "sổ đỏ" for Giấy chứng nhận or a Cần Thơ farmer for the đồng bằng sông Cửu Long limit. |
| `dieu_kien_ngoai_le` (condition or exception) | 6 | The answer depends on a condition, such as the administrative fine required before non-use leads to recovery in 2024. |

Ten questions carry a `year` filter and ten do not.

## Metadata filter

The only filter field is `year` (2013 or 2024), read from the law number of each source file, never inferred.
A question gets a filter only when the question itself names the version ("Theo Luật Đất đai 2013 ..."), so the value is supplied by the user before retrieval and never taken from the gold source.
Every question without a filter has the same answer in both laws, and all valid pages are listed as gold, so no unfiltered question is ambiguous between versions.
Six of the filtered questions come in pairs whose answers differ between the versions (q01/q02: 10 against 15 times the allocation limit, q03/q04: 45 against 30 days of mediation, q05/q06: rice land transfer forbidden against allowed with conditions).
These are the questions where configuration C should matter, because the other version's clause is a near-duplicate distractor.
Configuration C applies the filter only to these ten questions and runs the other ten exactly like B.

## Split

15 dev and 5 test, fixed in this file before the first retrieval run.

| Split | Questions | Filtered |
|---|---|---:|
| dev | q01, q02, q05, q06, q07, q08, q10, q11, q13, q14, q15, q16, q17, q19, q20 | 8 |
| test | q03, q04, q09, q12, q18 | 2 |

The split is by topic, to limit leakage.
Both questions of a version pair sit in the same split, and no test question shares an article with a dev question.
Residual risk: q13 (dev, extension of an investment project) and q18 (test, land term for an investment project) are both about projects, though they cite different articles.
q08 and q13 share a page (ldd2024_cb363 p77), and q14 and q20 share one (ldd2024_cb363 p6); all four are dev.

## Evidence verification

Each `evidence_quote` is copied from the normalised page text in `data/processed/pages.jsonl`, keeping source typos ("Ủy ban nhân cấp xã" in q03).
The verifier compares quotes whitespace-insensitively and fails if any quote is missing from its page, if a quote is copied into its question, or if the split or filter minimums are not met.
Result: 29 of 29 gold quotes found on their pages.
Quotes are kept to the shortest span that supports the answer, including its condition, so a 500-character chunk can contain them; q08 is the longest at 310 characters because its condition sits at the end of the clause.
Known boundary case: q04's article starts on ldd2024_cb367 p45 and its evidence is on p46.

## Selection rule, fixed before any run

The configuration used on test is chosen on dev only, in this order:

1. Highest dev Evidence Hit@3.
2. If two configurations are within one question of each other (1/15), higher dev Hit@1, with the same tie band.
3. Then the higher mean chunk coherence (/2) on the fixed 10 positions.
4. Then fewer chunks and lower ingest time.

Test is run once for each configuration after the choice is fixed, and no test result is used to change chunking, filtering, the model or the questions.

## Limits

The author wrote the questions, the answers and the gold labels, and searched the corpus for clauses while writing them, so the set leans toward clauses that are easy to find by keyword.
Paraphrase questions reduce this but do not remove it.
Twenty questions give coarse metrics: one question is 6.7 points on dev and 20 points on test.
