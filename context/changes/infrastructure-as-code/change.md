---
change_id: infrastructure-as-code
title: GitHub Actions CI/CD pipeline for build, test, and deploy
status: implementing
created: 2026-07-28
updated: 2026-07-30  # 8.12: upload-500 root cause found + fixed (AWS_S3_ADDRESSING_STYLE virtual-host→auto, ac69636+8024d3c); upload + enqueue work. NEW blocker: 512MB OOM kills the q2 worker mid-process_image (8.9); user upgrading to Hobby to resolve.
archived_at: null
---

## Notes

We shall build GitHub Actions pipeline to build and test every branch we push in the Pull Request, and deploy the application whenever the master branch completes testing successfully
