"""Read-only host readiness report; see --help and HOST_PREFLIGHT.md."""

from abstract_harness.preflight import main

if __name__ == "__main__":
    raise SystemExit(main())
