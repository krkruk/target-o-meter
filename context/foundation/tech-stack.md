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

## Why this stack

Solo developer building an ISSF target-scoring web app in 3 after-hours weeks with OAuth auth and computer-vision bullet hole detection. Django is the recommended default for (web, python) — it ships auth, ORM, admin, and migrations out of the box, covering FR-001 through FR-005 with minimal assembly. At hobbyist scale with a short timeline, the batteries-included approach beats lighter frameworks where you'd wire auth and data layers yourself. Computer vision (FR-007/FR-008) is a custom ML module that sits alongside Django as a service — no web framework ships this first-class, so the choice is framework-neutral. Render is the deployment target; CI runs on GitHub Actions with auto-deploy on merge.
