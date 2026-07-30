---
change_id: infrastructure-as-code
title: GitHub Actions CI/CD pipeline for build, test, and deploy
status: implementing
created: 2026-07-28
updated: 2026-07-30  # 8.6/8.9/8.12 RESOLVED. Upload 500: fixed virtual-host→auto addressing (8024d3c) + survived a Railway-side bucket suspension (plan-upgrade bug, sam-a station 3ede6443). Worker silent death at 1GB resolved by bumping pod to 2GB (eed12fa adds faulthandler + stage logging). Scoring confirmed working end-to-end: upload → enqueue → process_image → SUCCEEDED.
archived_at: null
---

## Notes

We shall build GitHub Actions pipeline to build and test every branch we push in the Pull Request, and deploy the application whenever the master branch completes testing successfully
