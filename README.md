# Agentic Label Engineering (ALE)

Typed task labels, an append-only board and a zero-LLM watchdog for
multi-agent coding work.

This is a stdlib-only, Python 3.9+ tool. No third-party runtime
dependencies; pytest is dev-only.

No Windows support. No network filesystems.

Run tests with: `uvx --python 3.9 pytest -q`

See `ale/schema/` for the label, roster and event JSON schemas, and
`examples/` for sample data.
