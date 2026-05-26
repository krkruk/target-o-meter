---
bootstrapped_at: 2026-05-24T21:07:00Z
starter_id: django
starter_name: Django
project_name: target-o-meter
language_family: python
package_manager: uv
cwd_strategy: native-cwd
bootstrapper_confidence: verified
phase_3_status: ok
audit_command: "pip-audit --format json"
---

## Hand-off

```yaml
---
starter_id: django
package_manager: uv
project_name: target-o-meter
hints:
  language_family: python
  team_size: solo
  deployment_target: render
  ci_provider: github-actions
  ci_default_flow: auto-deploy-on-merge
  bootstrapper_confidence: verified
  path_taken: standard
  quality_override: false
  self_check_answers: null
  has_auth: true
  has_payments: false
  has_realtime: false
  has_ai: true
  has_background_jobs: false
---
```

## Why this stack

Solo developer building an ISSF target-scoring web app in 3 after-hours weeks with OAuth auth and computer-vision bullet hole detection. Django is the recommended default for (web, python) — it ships auth, ORM, admin, and migrations out of the box, covering FR-001 through FR-005 with minimal assembly. At hobbyist scale with a short timeline, the batteries-included approach beats lighter frameworks where you'd wire auth and data layers yourself. Computer vision (FR-007/FR-008) is a custom ML module that sits alongside Django as a service — no web framework ships this first-class, so the choice is framework-neutral. Render is the deployment target; CI runs on GitHub Actions with auto-deploy on merge.

## Pre-scaffold verification

| Signal             | Value                | Severity | Notes                                              |
| ------------------ | -------------------- | -------- | -------------------------------------------------- |
| npm package        | not run              | N/A      | non-JS starter (python)                            |
| GitHub repo        | not run              | N/A      | docs_url (https://docs.djangoproject.com) is not a GitHub repo URL |

No recency signal available for this starter.

## Scaffold log

**Resolved invocation**: `django-admin startproject target_o_meter .`
**Strategy**: native-cwd
**Exit code**: 0
**Pre-flight files-to-touch**: manage.py, target_o_meter/__init__.py, target_o_meter/asgi.py, target_o_meter/settings.py, target_o_meter/urls.py, target_o_meter/wsgi.py
**Files written by CLI**: 6
**Pre-existing files preserved**: AGENTS.md, draft.md, context/ (entire tree)

Note: The card's `cmd_template` is `django-admin startproject {name} .`. For native-cwd, `{name}` substitutes to `.`, producing `django-admin startproject . .`, which Django rejects (`.` is not a valid Python identifier). The project name `target-o-meter` was sanitized to `target_o_meter` and used as the first argument: `django-admin startproject target_o_meter .`.

## Post-scaffold audit

**Tool**: pip-audit --format json
**Summary**: 3 CRITICAL, 3 HIGH, 9 MODERATE, 1 LOW
**Direct vs transitive**: not distinguished by this tool

Note: pip-audit scanned the full system Python environment. The freshly scaffolded Django project has no pinned dependencies yet (no requirements.txt or pyproject.toml). Findings below reflect the system-wide install, not just the project.

#### CRITICAL findings

**pillow 11.3.0 — CVE-2026-25990**
Out-of-bounds write when loading a specially crafted PSD image.
Fix: upgrade to 12.1.1.

**pillow 11.3.0 — CVE-2026-42309**
Heap buffer overflow via nested lists as coordinates in ImagePath.Path, ImageDraw.polygon, and ImageDraw.line.
Fix: upgrade to 12.2.0.

**pillow 11.3.0 — CVE-2026-42311**
Memory corruption via malicious PSD file, potentially resulting in arbitrary code execution. Incomplete fix for CVE-2026-25990 due to integer overflow in tile extent sums.
Fix: upgrade to 12.2.0.

#### HIGH findings

**lxml 6.0.1 — PYSEC-2026-87 (CVE-2026-41066)**
Untrusted XML input can read local files when resolve_entities=True (default).
Fix: upgrade to 6.1.0.

**pillow 11.3.0 — CVE-2026-42308**
Integer overflow in font glyph advances, leading to potential issues.
Fix: upgrade to 12.2.0.

**urllib3 2.6.3 — PYSEC-2026-141 (CVE-2026-44431)**
Cross-origin redirects from ProxyManager forward sensitive headers.
Fix: upgrade to 2.7.0.

#### MODERATE findings

**idna 3.10 — CVE-2026-45409**: DoS via crafted argument to idna.encode(). Fix: 3.15.
**pillow 11.3.0 — CVE-2026-40192**: Decompression bomb in FITS images. Fix: 12.2.0.
**pillow 11.3.0 — CVE-2026-42310**: Infinite loop in PDF parser via cyclic Prev pointers. Fix: 12.2.0.
**pip 25.1.1 — CVE-2025-8869**: Tar symlink extraction issue. Fix: 25.3.
**pip 25.1.1 — CVE-2026-1703**: Path traversal in wheel extraction. Fix: 26.0.
**pip 25.1.1 — CVE-2026-6357**: Self-update imports modules after wheel install. Fix: 26.1.
**pygments 2.19.1 — CVE-2026-4539**: ReDoS in AdlLexer. Fix: 2.20.0.
**requests 2.32.5 — CVE-2026-25645**: Predictable temp filename in extract_zipped_paths(). Fix: 2.33.0.
**urllib3 2.6.3 — PYSEC-2026-142 (CVE-2026-44432)**: Excessive resource consumption on partial decompression. Fix: 2.7.0.

#### LOW / INFO findings

**pip 25.1.1 — CVE-2026-3219**: Concatenated tar/ZIP files handled as ZIP regardless. Fix: 26.1.

## Hints recorded but not acted on

| Hint                       | Value                              |
| -------------------------- | ---------------------------------- |
| bootstrapper_confidence    | verified                           |
| quality_override           | false                              |
| path_taken                 | standard                           |
| self_check_answers         | null                               |
| team_size                  | solo                               |
| deployment_target          | render                             |
| ci_provider                | github-actions                     |
| ci_default_flow            | auto-deploy-on-merge               |
| has_auth                   | true                               |
| has_payments               | false                              |
| has_realtime               | false                              |
| has_ai                     | true                               |
| has_background_jobs        | false                              |

## Next steps

Next: a future skill will set up agent context (CLAUDE.md, AGENTS.md). For now, your project is scaffolded and verified — happy hacking.

Useful manual steps in the meantime:
- `git init` (if you have not already) to start your own repo history.
- Review any `.scaffold` siblings the conflict policy created and decide which version of each file to keep.
- Address audit findings per your project's risk tolerance — the full breakdown is in this log.
