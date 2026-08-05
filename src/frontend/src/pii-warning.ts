// The single source of truth for the PII / LLM-training warning wording.
//
// Verbatim user wording (decision: "keep my original" — do not paraphrase).
// Rendered as a role="note" callout on EVERY upload surface (/upload and the
// /capture fallback) so the warning is visible regardless of platform (mobile,
// PC, tablet) or which route the user reaches it through.
export const PII_WARNING =
  'The data is used to train LLM models. Do not upload Personal Identifiable Information. ' +
  'By uploading the image, you agree to effectively make this information public. ' +
  'Think about it and proceed responsibly.';
