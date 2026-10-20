# Contributing

Thanks for your interest in improving this prototype.

## Reporting bugs and requesting features

Open an issue in the repository where you found this project. Describe what you
did, what you expected, and what happened. Do not attach real camera footage or
any personal data; reproduce problems with the synthetic generator
(`scripts/gen_synthetic.py`) instead.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Set up the environment: `uv sync`.
3. Make your change and add or update tests under `tests/`.
4. Run `uv run ruff check .` and `uv run pytest`.
5. Run a secret scan before committing (for example `uvx detect-secrets scan`).
6. Open a pull request with a clear description of the change.

## Data rules

- Only synthetic data or public datasets with a compatible license may be added.
- No real video, no images of real people, no account identifiers or credentials.

## Security issues

See [SECURITY.md](SECURITY.md). Do not open public issues for security problems.

## License

By contributing, you agree that your contributions are licensed under the MIT-0 license in [LICENSE](LICENSE).
