"""Advanced: Custom Tools — Register your own Python functions as agent tools.

Usage:
    python examples/advanced/custom_tools.py
"""

from aegis_cognition import Agent


# Define custom tools as plain Python functions
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression safely."""
    import ast
    import operator

    allowed = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }

    try:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and type(node.op) not in allowed:
                raise ValueError(f"Operator not allowed: {type(node.op).__name__}")
        result = eval(compile(tree, "<string>", "eval"), {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"Error: {e}"


task = """
Calculate the following:
1. (2 + 3) * 4
2. 2 ** 10
3. 100 / 7 (rounded to 2 decimal places)

Use the calculate function for each.
"""

agent = Agent(
    task=task,
    trust_level="DEV",
)

result = agent.run()
print(result.output)