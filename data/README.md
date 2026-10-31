# Authoritative company dataset

`companies.csv` is a byte-for-byte copy of the supplied root file `india_rtl_asic_companies.csv`. It has one column, `Company Name`, and 66 original, unique company names. There are no invalid rows or exact duplicates.

SHA-256: `e112ab8939225aa78d1bf62c6e749cc9d3e29afa0765f6de38173ccfa9087503`

The source is never edited by application imports. Imports preserve exact names, including punctuation, Unicode and whitespace. Company inclusion does not establish a verified India office or RTL/ASIC design team.

`connectiq.db` is the ignored local development database. Production uses PostgreSQL. The source CSV, migrations and application code are committed to GitHub; local databases, credentials and personal exports are not.
