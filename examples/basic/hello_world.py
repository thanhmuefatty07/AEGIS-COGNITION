"""Hello World — Your first AEGIS agent.

Usage:
    python examples/basic/hello_world.py
"""

from aegis_cognition import Agent

agent = Agent(task="Say hello and introduce yourself as AEGIS-COGNITION")
result = agent.run()
print(result.output)