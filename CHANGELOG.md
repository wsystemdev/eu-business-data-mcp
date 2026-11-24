# Changelog

## 1.1.0 (2026-11-24)
- Consistent verb_noun tool names. Renamed: `verify_batch` -> `verify_vat_batch`, `kyb_check` -> `check_kyb`,
  `einvoice_route` -> `get_einvoice_route`, `due_diligence_report` -> `get_due_diligence_report`.
  **Backward compatible:** the old names still work in `tools/call` (hidden aliases, not listed in `tools/list`).
- New tools: `lookup_company_batch` (up to 25 companies, same as REST `POST /company/batch`) and
  `screen_sanctions_batch` (up to 50 names, same as REST `POST /sanctions/screen/batch`).
- Every tool description now says when to use it (and which tool to use instead), its inputs and its output fields;
  `check_kyb` (quick verdict) vs `get_due_diligence_report` (full report) vs `get_einvoice_route` (how to invoice a buyer) is explicit.
- README tool table now lists exactly the 13 tools this package registers; hosted-only tools are named separately.
- Not added (the API has no such endpoint): a "get VIES result by consultation number" tool. VIES does not offer a lookup
  by consultation number; the number is returned by `verify_vat` with `requester` and is your audit proof.

## 1.0.5
- Tool titles, readOnlyHint annotations and argument descriptions; all 11 tools register in stdio/http mode.
