# Contributing to gamAI

Thanks for contributing. We apply the same standard to contributions that we
apply to our own claims: **evidence over assertion**.

## Principles

- **Evidence over assertion.** Every number in code, docs, and benchmarks must
  be reproducible. A PR that adds a benchmark result must include the command
  that produced it.
- **Refusal is a feature.** Do not weaken safety gates (prompt-injection
  defense, proof checks, fail-closed behavior) to make a test pass.
- **Small-N results are labeled.** If a result is from a small sample, say so.
- **Price the isolation.** Changes to isolation/trust code need tests.

## How to contribute

1. Fork the repository and create a branch.
2. Make one focused change per PR.
3. Add or adjust tests; run the suite (see each repo's README).
4. Open a PR and link the related issue if one exists.

## License

By contributing, you agree that your contribution is licensed under the
repository's license (MIT or Apache-2.0).

## Getting started

Look for issues labeled `good first issue`.
