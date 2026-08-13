"""Simple Web Research — Extract structured data from a question.

Usage:
    python examples/basic/simple_research.py
"""

from aegis_cognition import Agent

task = """
Find the top 5 AI agent frameworks on GitHub.
For each one, return:
- Name
- Stars
- Main programming language
- Brief description (1 sentence)

Format the output as a markdown table.
"""

agent = Agent(task=task)
result = agent.run()
print(result.output)