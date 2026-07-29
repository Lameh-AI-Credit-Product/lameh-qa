"""
Lameh Intelligence - LLM judge client
=======================================
Thin wrapper around Claude via AWS Bedrock, used by llm_judge.py. Kept
separate from it so the transport can be swapped/mocked independently of the
rubric in tests.
"""

from anthropic import AnthropicBedrock

from config import AWS_ACCESS_KEY_ID, AWS_BEDROCK_MODEL_ID, AWS_REGION, AWS_SECRET_ACCESS_KEY

# The verdict is a JSON object carrying findings lists (untagged values, with
# a quoted figure and surrounding context each), not just a few booleans - a
# response with a dozen untagged figures runs well past the old 1024 ceiling.
# A truncated reply is unparseable JSON, which surfaces as judge_error and
# leaves every judged dimension unscored, so this is deliberately generous.
DEFAULT_MAX_TOKENS = 4096


def _client():
    return AnthropicBedrock(
        aws_access_key=AWS_ACCESS_KEY_ID,
        aws_secret_key=AWS_SECRET_ACCESS_KEY,
        aws_region=AWS_REGION,
    )


def ask_judge(prompt, max_tokens=DEFAULT_MAX_TOKENS):
    """Sends `prompt` to the judge model and returns its text reply. Callers
    (e.g. helpfulness.py) own the prompt template/rubric and any JSON
    parsing of the response - this function just makes the call."""
    response = _client().messages.create(
        model=AWS_BEDROCK_MODEL_ID,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


if __name__ == "__main__":
    print(ask_judge("Reply with exactly the word: pong"))
