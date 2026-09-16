# Full Audit Manifest

The public repository deliberately excludes the complete historical audit
record. The retained local record is approximately 17,539 evidence files and
approximately 86.8 GB.

## Included categories in the historical record

- frozen candidate queues and selection receipts;
- original and counterfactual execution evidence;
- request and response traces;
- initial and final state snapshots and digests;
- evaluator outputs and certificate records;
- calibration, diagnosis, errata, and reporting-recovery records;
- E1/E2/E3 validation artifacts;
- opportunity scans and failed strengthening attempts;
- benchmark databases and generated runtime data.

## Why it is not distributed here

The full record is too large for a lightweight code release, contains
benchmark-native state and database snapshots, and includes artifacts whose
redistribution depends on the upstream benchmark license. Complete traces are
also unnecessary for checking the compact result ledger and running the toy
implementation.

## Public substitutes

The public release contains small JSON ledgers, hashes, result projections,
errata, benchmark commit pins, and representative case metadata. These are
enough to identify the source and scope of each reported result without
pretending that the whole archive is available.

## Possible future archival route

If an external artifact archive is created later, it should publish a separate
license review, manifest, checksums, access policy, and versioned download
instructions. No such archive URL is asserted by this repository.
