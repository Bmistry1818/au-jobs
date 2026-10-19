# Data sources

Each source the pipeline uses or considers, what it is for, its licence or terms, and its
current status. Update this file whenever a source is added, dropped or changes status.

| Source | Used for | Licence or terms | Status |
| --- | --- | --- | --- |
| [JSA occupation profiles](https://www.jobsandskills.gov.au/data/occupation-and-industry-profiles) | Employment, median earnings, education, task text | CC BY 4.0 (check the licence on each release) | In the pipeline (`aujobs extract`) |
| [JSA employment projections](https://www.jobsandskills.gov.au/data/employment-projections) | 5-year and 10-year growth | CC BY 4.0 (check each release) | In the pipeline (`aujobs extract`) |
| [JSA Internet Vacancy Index](https://www.jobsandskills.gov.au/work/internet-vacancy-index) | Monthly online job ads per occupation; 12-month change | Check the terms on the download page | Parser written and tested on a synthetic workbook. **Not yet checked against a live release.** |
| ABS Job Vacancies (for example the [May 2026 release](https://www.abs.gov.au/media-centre/media-releases/job-vacancies-fall-may-2026)) | Macro cross-check of total vacancies | ABS publications are generally CC BY 4.0. Verify per product. | Not integrated. Cross-check by hand. |
| ABS OSCA ([consultation draft, August 2026](https://www.abs.gov.au/statistics/classifications/consultation-draft-occupation-standard-classification-australia-osca/aug-2026/browse-classification/3)) | Replacement occupation classification | ABS publication. Verify. | Consultation draft. ABS gives March 2027 as the expected final release. **No crosswalk is shipped.** |
| Anthropic Claude API | AI exposure scoring | Anthropic commercial terms | In the pipeline (`aujobs score`) |
| Indeed Hiring Lab, Australia | Context only | Copyrighted. Permission is required from Indeed. | **Not used.** Do not scrape. |
| Commercial job-ad feeds (for example SEEK, Adzuna) | Considered for demand | Terms must be checked before any use | **Not used.** |

## Policy

* Only sources whose licence allows reuse with attribution go into the pipeline.
* Job boards are not scraped. Where a source does not provide a download, it is not used.
* Every downloaded file is recorded in `data/raw/manifest.json` with its URL, retrieval
  time and SHA-256 checksum. The published site cites these.
* A statement attributed to a source (for example, "ABS reports...") is checked against
  the source's own page before it is published.
* A source's status moves to "in the pipeline" only after its parser has been run
  against a real release and its output has been reviewed.
