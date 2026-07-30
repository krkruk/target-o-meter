---
change_id: infrastructure-as-code
title: GitHub Actions CI/CD pipeline for build, test, and deploy
status: implementing
created: 2026-07-28
updated: 2026-07-30  # 8.6/8.12: upload 500 had TWO causes. (1) fixed: AWS_S3_ADDRESSING_STYLE virtual-host→auto (8024d3c) — upload worked at 18:11. (2) NEW, Railway-side: Free→Hobby upgrade left the storage org backing the bucket SUSPENDED (sam-a, station 3ede6443) — same upload now 403s on HeadObject + owner locked out of dashboard. Needs Railway backend reactivation; 8.6 reopened. OOM (8.9) resolved by Hobby upgrade (1GB).
archived_at: null
---

## Notes

We shall build GitHub Actions pipeline to build and test every branch we push in the Pull Request, and deploy the application whenever the master branch completes testing successfully
