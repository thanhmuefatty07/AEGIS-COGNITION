"""Amazon Scraper — Browser automation example.

Requires: pip install aegis-cognition[browser]
Usage:    python examples/browser/amazon_scraper.py
"""

from aegis_cognition import Agent

agent = Agent(
    task="""
    Go to amazon.com and find the best-selling laptops.
    Extract the top 3 laptops with:
    - Product name
    - Price
    - Rating (stars)
    - Number of reviews
    
    Return as a formatted list.
    """,
    browser=True,
    trust_level="DEV",
)

result = agent.run()
print(result.output)