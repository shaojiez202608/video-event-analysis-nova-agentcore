# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-21

### Added

- Camera-clip event analysis pipeline: PyAV frame sampling (or whole-clip video input), Amazon Nova
  through the Converse API with a forced output tool, hard validation, retry and explicit fallback,
  cross-segment merging, risk floors, localised templates (zh-CN, en-US, ru-RU), structured watch
  rules, keyword search, Q&A with one read-only tool, deterministic daily summary.
- AgentCore Runtime (direct code deployment, arm64) and an S3-triggered dispatcher Lambda (arm64),
  deployed with one CDK stack; cdk-nag AwsSolutions with per-construct acknowledgements.
- Synthetic evaluation set (generator + versioned labels), mock model, evaluation with a negative
  control, strategy comparison with repeated runs.
- Runtime and media-bucket resource policies; SQS on-failure queue for the dispatcher; runtime log
  group retention via a custom resource.
- Parameter templates with synth-time validation, preflight, guarded deploy (`ALLOW_DESTRUCTIVE`),
  paid-command guard (`ALLOW_PAID`), cleanup with confirmations, failing residual check, AWS
  end-to-end and failure-drill scripts, Makefile.
- CI (lint, tests, synth + cdk-nag, gitleaks with canary), manual deploy-verify workflow (OIDC),
  Dependabot, CODEOWNERS, issue and PR templates, governance and security documents.

### Fixed (review round before the first release)

- Model-call errors produce stored partial/fallback results instead of dropping the clip.
- Validation messages and logs no longer contain model text.
- The final partial interval of a clip is sampled.
- Per-request overrides are bounded; a per-clip model-call cap applies.
- High-risk alerts survive custom watch rules; night windows match by overlap.
- Truncated searches are reported; tool time zones are converted correctly.
