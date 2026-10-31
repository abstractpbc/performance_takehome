"""CANDIDATE-BUILT STAND-IN: reference runner for ``workload.json``.

The organizer's fixed measurement runner is unreleased (``docs/RELEASE_STATUS.md``). This
async ``httpx`` runner executes the declarative plan against any OpenAI-compatible
streaming server and records raw evidence in the ``suite/schema`` format. It never
substitutes timestamps and never trusts server ``usage``.
"""
