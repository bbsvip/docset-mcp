# Contributing

Contributions are welcome, particularly for additional docset index formats, robust mirror handling, MCP client compatibility, and tests.

## Development environment

Python 3.11+ is required. Keep the source code in `source/` and the virtual environment in `venv/` (or `.venv/`); do not create an installable Python package solely to run the server.

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python -m pip install pytest
./venv/bin/python -m pytest -q tests
```

On Windows, use the executable under `venv\Scripts\python.exe`.

## Before opening a pull request

- Add or update offline test fixtures for behavior changes.
- Confirm that `python -m pytest -q tests` passes.
- Run `docker compose config` if Compose/Docker configuration has changed.
- Keep user-facing text, MCP tool descriptions, and agent prompts in English.
- Do not commit downloaded docsets, SQLite databases, `.part` files, credentials, or virtual environments.
- Record any network-dependent behavior that you could not validate.

## Third-party content

This project is not a mirror or redistribution repository. Do **not** add docset archives or copied proprietary documentation. Respect the [Kapeli feeds usage terms](https://github.com/Kapeli/feeds#license) and any content-specific license.

## Security reports

Please avoid posting secrets or private data in public issues. The MCP HTTP endpoint is unauthenticated by default and intended for trusted local use; deployments on a public network require external access controls.
