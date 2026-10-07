# Maintaining the user guide

[User-guide home](README.md)

Owner: **TOSCA product/operations team**  
Last verified: **2 October 2026**  
Verified revision: **`cb53324`**

## When documentation must change

Update the guide in the same change whenever work modifies:

- an admin field, label, help text, choice, validation rule, or form layout;
- a role, permission, organization scope, or authentication mapping;
- synchronization, publishing, visibility, or deletion behavior;
- Editor.js block support or media handling;
- Event Type, taxonomy, recurrence, style, or sprite workflows;
- frontend routes used by Footer documents or public content.

## Review checklist

1. Compare the guide with the Django admin form and model validation.
2. Test the workflow with a non-superuser documentation account.
3. Verify both successful and common error paths.
4. Update related cross-links and the visibility matrix.
5. Replace screenshots when labels or layout change.
6. Sanitize screenshots and alternative text.
7. Update the verification date and Git revision in this file and the guide home.
8. Run Markdown/link checks and review the rendered pages.

## Screenshot standard

- Use a dedicated documentation organization and synthetic sample data.
- Capture the smallest area that preserves context.
- Use consistent browser dimensions and light/dark theme.
- Hide usernames, email addresses, credentials, tokens, internal hostnames, and private content.
- Add a caption explaining the decision or action shown.
- Store files in `docs/user-guide/images/` with stable lowercase-hyphenated names.
- Provide meaningful Markdown alternative text; do not repeat the caption verbatim.

## Source of truth

The running admin UI and enforced backend validation are authoritative. When this guide disagrees with the software, stop the workflow, confirm the behavior in a safe environment, and update either the implementation or the documentation rather than teaching users a workaround.

