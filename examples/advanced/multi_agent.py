"""Multi-Agent — Run multiple agents in parallel for complex tasks.

Usage:
    python examples/advanced/multi_agent.py
"""

import asyncio
from aegis_cognition import Agent


async def research_topic(topic: str) -> str:
    agent = Agent(task=f"Research: {topic}. Give me a 3-sentence summary.")
    result = await agent.arun()
    return result.output


async def main():
    topics = [
        "Latest developments in AI agent frameworks (2026)",
        "Browser automation tools comparison",
        "Rust vs Python for AI infrastructure",
    ]

    results = await asyncio.gather(*(research_topic(t) for t in topics))

    for topic, summary in zip(topics, results):
        print(f"\n{'='*60}")
        print(f"Topic: {topic}")
        print(f"{'-'*60}")
        print(summary)


if __name__ == "__main__":
    asyncio.run(main())