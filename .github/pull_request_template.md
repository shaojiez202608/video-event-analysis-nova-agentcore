## What and why

<!-- One change per PR. Use a Conventional Commits title, e.g. "fix(pipeline): ..." -->

## Checks run

- [ ] `make lint`
- [ ] `make test`
- [ ] `make synth` (cdk-nag clean; new acknowledgements have a reason in SECURITY-NOTES.md)
- [ ] `make scan-ci` (no secrets, no account ids, no real media)
- [ ] `make eval-mock` (negative control still reported as failing)
- [ ] Deployed check (`make deploy verify destroy verify-cleanup`, sandbox only), if infrastructure changed

## Not tested

<!-- List what you did not run or could not verify, and why. -->

## Docs

- [ ] README / docs / DECISIONS / VERIFICATION updated where behaviour changed
