# Dependency Management Guidelines

This project now uses `pyproject.toml` as the single source of truth for Python dependencies.

## Policies
- Do NOT edit `requirements.txt` manually. It will be regenerated from `pyproject.toml` (e.g. via `uv pip compile` or `pip-compile` if adopted).
- Version ranges are constrained to compatible minor releases to reduce breakage while allowing security updates.
- Prefer adding new libs with an upper bound `<next_major.0.0`.

## Updating Dependencies
1. Modify `[project].dependencies` in `pyproject.toml`.
2. For dev/test tools, modify `[dependency-groups].dev`.
3. Regenerate lockfile: `uv lock` (or tool of choice) and export `requirements.txt` if needed.

## Rationale
- Removes divergence between `requirements.txt` and `pyproject.toml`.
- Enables reproducible builds with a single lock source.
- Keeps MCP + LangChain ecosystem versions aligned.

## MCP / LangChain Notes
- `langgraph` pinned due to API compatibility.
- `fastmcp` pinned for server interoperability.
- `langchain-mcp-adapters` pinned until upstream stabilizes.

## Security
- Auth-related packages grouped and capped: ensure timely updates with minor bumps.

## Future Improvements
- Introduce automated dependabot / renovate configuration.
- Add SCA (Software Composition Analysis) pipeline step.
