"""GitHub Trending — Browser automation to scrape GitHub.

Requires: pip install aegis-cognition[browser]
Usage:    python examples/browser/github_trending.py
"""

from aegis_cognition import Agent

agent = Agent(
    task="""
    Go to github.com/trending and find the top 5 trending repositories today.
    For each one, extract:
    - Repository name (owner/repo format)
    - Description
    - Stars gained today
    - Primary language
    
    Return as a markdown table.
    """,
    browser=True,
    trust_level="DEV",
)

result = agent.run()
print(result.output)