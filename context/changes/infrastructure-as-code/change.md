---
change_id: infrastructure-as-code
title: GitHub Actions CI/CD pipeline for build, test, and deploy
status: implementing
created: 2026-07-28
updated: 2026-07-30  # 8.12 step 1: prod LOGGING + upload/worker stage logging landed (ac69636), deployed. Awaiting prod upload retry to read the 500 traceback + fix root cause.
archived_at: null
---

## Notes

We shall build GitHub Actions pipeline to build and test every branch we push in the Pull Request, and deploy the application whenever the master branch completes testing successfully
