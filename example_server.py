"""
Example MCP Server using FastMCP
"""
import os
import json
from mcp.server.fastmcp import FastMCP

# Create an MCP server
mcp = FastMCP(
    name="Example Knowledge Base",
    host="0.0.0.0",
    port=8050,
)

@mcp.tool()
def get_knowledge_base() -> str:
    """Retrieve the entire knowledge base as a formatted string.

    Returns:
        A formatted string containing all Q&A pairs from the knowledge base.
    """
    try:
        # Create sample knowledge base data
        kb_data = [
            {
                "question": "What is our company's vacation policy?",
                "answer": "Employees are entitled to 20 days of vacation per year, plus national holidays. Vacation requests should be submitted at least 2 weeks in advance."
            },
            {
                "question": "How do I submit an expense report?", 
                "answer": "Expense reports can be submitted through the company portal. Include all receipts and fill out the required forms. Reports are processed within 5 business days."
            },
            {
                "question": "What are the office hours?",
                "answer": "Office hours are Monday to Friday, 9:00 AM to 6:00 PM. Remote work is allowed on Fridays with manager approval."
            }
        ]

        # Format the knowledge base as a string
        kb_text = "Here is the retrieved knowledge base:\n\n"

        for i, item in enumerate(kb_data, 1):
            question = item.get("question", "Unknown question")
            answer = item.get("answer", "Unknown answer")
            kb_text += f"Q{i}: {question}\n"
            kb_text += f"A{i}: {answer}\n\n"

        return kb_text
        
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def add(a: int, b: int) -> str:
    """Add two numbers together.
    
    Args:
        a: First number
        b: Second number
        
    Returns:
        The sum as a string
    """
    result = a + b
    return f"The sum of {a} and {b} is {result}"

@mcp.tool()
def calculate(expression: str) -> str:
    """Safely evaluate a mathematical expression.
    
    Args:
        expression: Mathematical expression to evaluate
        
    Returns:
        The result of the calculation
    """
    try:
        # Only allow safe mathematical operations
        allowed_chars = set('0123456789+-*/().')
        if not all(c in allowed_chars or c.isspace() for c in expression):
            return "Error: Invalid characters in expression"
        
        result = eval(expression)
        return f"The result of '{expression}' is {result}"
    except Exception as e:
        return f"Error evaluating expression: {str(e)}"

# Run the server
if __name__ == "__main__":
    print("Starting Example MCP Server...")
    print("Available transports: stdio, sse")
    print("For stdio: python example_server.py")
    print("For SSE: Server will be available at http://localhost:8050/sse")
    mcp.run(transport="stdio")
