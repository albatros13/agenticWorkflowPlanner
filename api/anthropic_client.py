import base64
import json
import os
from anthropic import Anthropic
import logging
from agents.text_to_bpmn.pipeline.content_provider import get_full_paths, encode_image_with_type, get_html_content
from anthropic import APIError

DEFAULT_ANTHROPIC = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    print("⚠️ Anthropic API key not set — skipping Anthropic features")
#if not ANTHROPIC_API_KEY:
#    logger.error("❌ Configuration error: ANTHROPIC_API_KEY not found in environment or .env file.")
#   raise RuntimeError(
#        "Configuration error: ANTHROPIC_API_KEY not found. "
#       "Please set it as an environment variable or in your .env file."
#    )

# Initialize Claude client
llm_client = Anthropic(api_key=ANTHROPIC_API_KEY)


def ask_anthropic_structured(*, task, system, prompt, schema, max_tokens=4096, model=DEFAULT_ANTHROPIC):
    """Force a single tool call so Claude returns JSON conforming to ``schema``.

    This is the actual LLM call behind the pipeline's ``AnthropicProvider``. It forces
    structured output by exposing one tool and requiring the model to call it.

    Returns ``(data: dict, raw_text: str, model: str)``. Raises ``RuntimeError`` when the
    key is missing or the model returns no tool_use block; SDK/API errors propagate.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot call Anthropic.")
    tool = {
        "name": "emit_" + task,
        "description": f"Return the structured result for task '{task}'.",
        "input_schema": schema,
    }
    response = llm_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}],
        timeout=120,
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input, json.dumps(block.input), model
    raise RuntimeError("Anthropic response contained no tool_use block")


def ask_anthropic_text_llm(prompt, model=DEFAULT_ANTHROPIC):
    """
    Simple text-only LLM call for Anthropic.
    """
    try:
        response = llm_client.messages.create(
            model=model,
            max_tokens=120,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Anthropic text LLM request failed: {e}")
        return str(e)


def ask_anthropic_chat(system_prompt, messages, model=DEFAULT_ANTHROPIC, max_tokens=1024):
    """
    Multi-turn text chat call for Anthropic.

    system_prompt: string used as the top-level system instruction.
    messages: list of {"role": "user"|"assistant", "content": str} dicts.
    """
    try:
        response = llm_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            timeout=120,
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
    except APIError as e:
        logger.error(f"Anthropic chat request failed: {e}")
        return "The assistant is temporarily unavailable due to an Anthropic API error."
    except Exception as e:
        logger.error(f"Unexpected error in Anthropic chat request: {e}")
        return "The assistant is temporarily unavailable. Please try again."


def run_anthropic_tool_loop(system_prompt, messages, tools, executor,
                            model=DEFAULT_ANTHROPIC, max_tokens=1024, max_iters=6):
    """
    Manual agentic loop for Anthropic tool use.

    tools:    list of Anthropic tool schemas ({name, description, input_schema}).
    executor: callable(name, input_dict) -> str result (sent back as tool_result).
    Returns the model's final text answer.
    """
    convo = list(messages)
    last = None
    for _ in range(max_iters):
        last = llm_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=tools,
            messages=convo,
            timeout=120,
        )
        if last.stop_reason != "tool_use":
            break
        # Preserve the assistant turn (incl. tool_use blocks) verbatim.
        convo.append({"role": "assistant", "content": last.content})
        tool_results = []
        for block in last.content:
            if block.type == "tool_use":
                try:
                    output = executor(block.name, block.input)
                except Exception as e:
                    logger.warning(f"Tool '{block.name}' failed: {e}")
                    output = f"Error running tool: {e}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        convo.append({"role": "user", "content": tool_results})

    if last is None:
        return "The assistant is temporarily unavailable. Please try again."
    return "".join(b.text for b in last.content if b.type == "text").strip()


def get_image_description_from_file(image_path, question="Describe this image", model=DEFAULT_ANTHROPIC):
    """
    Uses Anthropic Claude 3.5 to generate a description of an image.
    """
    try:
        # Encode the image to base64
        def encode_image(image_path):
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode("utf-8")

        base64_image = encode_image(image_path)

        # Claude API call with multimodal input
        response = llm_client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64_image,
                            },
                        },
                    ],
                }
            ],
        )

        # Return Claude’s textual output
        return response.content[0].text

    except Exception as e:
        return str(e)


def ask_anthropic_llm(question, image_paths, prompt, model=DEFAULT_ANTHROPIC):
    full_paths = get_full_paths(image_paths)
    if not full_paths:
        return "Error: No images could be loaded. Please check the image paths."

    content = [
        {"type": "text", "text": prompt},
    ]

    image_inputs = [encode_image_with_type(path) for path in full_paths]

    for img in image_inputs:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img["type"],
                "data": img["data"],
            },
        })

    # Add the main question
    content.append({"type": "text", "text": f"Question: {question}"})

    # Send to Claude API
    try:
        response = llm_client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[{"role": "user", "content": content}],
            timeout=120
        )

        # Extract text blocks from response
        return "".join(
            block.text for block in response.content if block.type == "text"
        )

    except APIError as e:
        print(f"⚠️ Anthropic API error: {e}")
        return "LLM request failed due to an Anthropic API error."

    except Exception as e:
        print(f"⚠️ Unexpected error in Claude request: {e}")
        return "LLM request failed: service temporarily unavailable or timed out."


def ask_anthropic_llm_html(question, html_paths, prompt, model=DEFAULT_ANTHROPIC):
    if not html_paths:
        return "Error: No HTML paths provided."

    html_pages = get_html_content(html_paths)

    # Build content blocks for Claude API
    content_blocks = [
        {"type": "text", "text": prompt},
    ]

    # Add HTML page text blocks
    for i, html in enumerate(html_pages, start=1):
        content_blocks.append({
            "type": "text",
            "text": f"HTML Page {i}:\n{html}"
        })

    # Add user question
    content_blocks.append({"type": "text", "text": f"Question: {question}"})

    # Call Anthropic API
    try:
        response = llm_client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[{"role": "user", "content": content_blocks}],
            timeout=120
        )

        # Extract only text blocks from the output
        resp = "".join(
            block.text for block in response.content if block.type == "text"
        )
        # content = '\n'.join(content_blocks)
        # print("Prompt", content)
        return resp

    except APIError as e:
        print(f"⚠️ Anthropic API error: {e}")
        return "LLM request failed due to an Anthropic API error."

    except Exception as e:
        print(f"⚠️ Unexpected error in Claude request: {e}")
        return "LLM request failed: service temporarily unavailable or timed out."
