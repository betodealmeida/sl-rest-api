"""Convenience entry point: ``python -m semantic_api`` runs the dev server."""

from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run("semantic_api.app:app", host="0.0.0.0", port=8002, reload=False)


if __name__ == "__main__":
    main()
