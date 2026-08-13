"""Translation example — one-liner agent invocation.

Usage:
    python examples/basic/translation.py
"""

from aegis_cognition import run

text = """
Artificial intelligence is transforming how we build software.
Developers can now delegate complex tasks to AI agents,
freeing up time for creative problem-solving and architecture.
"""

result = run(f"Translate the following text to Vietnamese:\n\n{text}")
print(result.output)