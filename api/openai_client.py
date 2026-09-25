import json
import os
from openai import OpenAI
import logging
from agents.text_to_bpmn.pipeline.content_provider import get_full_paths, get_html_content, encode_image

DEFAULT_OPENAI = os.getenv("OPENAI_MODEL", "gpt-5")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.warning("⚠️ OPENAI_API_KEY not set — OpenAI features will be unavailable")

llm_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def ask_openai_structured(*, task, system, prompt, schema, max_tokens=4096, model=DEFAULT_OPENAI):
    """Force a function tool call so the model returns JSON conforming to ``schema``.

    This is the actual LLM call behind the pipeline's ``OpenAIProvider``.

    Returns ``(data: dict, raw_text: str, model: str)``. Raises ``RuntimeError`` when the
    key is missing or the model returns no tool call; SDK/API errors propagate.
    """
    if not llm_client:
        raise RuntimeError("OPENAI_API_KEY not set; cannot call OpenAI.")
    fn_name = "emit_" + task
    response = llm_client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        timeout=120,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": fn_name,
                "description": f"Return the structured result for task '{task}'.",
                "parameters": schema,
            },
        }],
        tool_choice={"type": "function", "function": {"name": fn_name}},
    )
    message = response.choices[0].message
    if not message.tool_calls:
        raise RuntimeError("OpenAI response contained no tool call")
    raw = message.tool_calls[0].function.arguments
    return json.loads(raw), raw, model


def ask_openai_text_llm(prompt, model=DEFAULT_OPENAI):
    if not llm_client:
        return "OpenAI unavailable: OPENAI_API_KEY not set."
    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"OpenAI text LLM request failed: {e}")
        return str(e)


def ask_openai_chat(system_prompt, messages, model=DEFAULT_OPENAI, max_tokens=1024):
    """
    Multi-turn text chat call for OpenAI.

    system_prompt: string used as the system message.
    messages: list of {"role": "user"|"assistant", "content": str} dicts.
    """
    if not llm_client:
        return "OpenAI unavailable: OPENAI_API_KEY not set."
    try:
        response = llm_client.chat.completions.create(
            model=model,
            timeout=120,
            messages=[{"role": "system", "content": system_prompt}, *messages],
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"OpenAI chat request failed: {e}")
        return "The assistant is temporarily unavailable. Please try again."


def run_openai_tool_loop(system_prompt, messages, anthropic_tools, executor,
                         model=DEFAULT_OPENAI, max_iters=6):
    """
    Manual agentic loop for OpenAI function calling.

    anthropic_tools: tool schemas in Anthropic shape ({name, description, input_schema});
                     converted to OpenAI's function-tool shape internally.
    executor:        callable(name, input_dict) -> str result.
    Returns the model's final text answer.
    """
    import json as _json

    if not llm_client:
        return "OpenAI unavailable: OPENAI_API_KEY not set."

    tools = [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t["input_schema"],
        },
    } for t in anthropic_tools]

    convo = [{"role": "system", "content": system_prompt}, *messages]
    msg = None
    for _ in range(max_iters):
        resp = llm_client.chat.completions.create(
            model=model, messages=convo, tools=tools, timeout=120,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            break
        convo.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            } for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            try:
                args = _json.loads(tc.function.arguments or "{}")
            except _json.JSONDecodeError:
                args = {}
            try:
                output = executor(tc.function.name, args)
            except Exception as e:
                logger.warning(f"Tool '{tc.function.name}' failed: {e}")
                output = f"Error running tool: {e}"
            convo.append({"role": "tool", "tool_call_id": tc.id, "content": output})

    if msg is None:
        return "OpenAI request failed."
    return (msg.content or "").strip()


def get_image_description_from_file(image_path, question, model=DEFAULT_OPENAI):
    if not llm_client:
        return "OpenAI unavailable: OPENAI_API_KEY not set."
    try:
        # Getting the base64 string
        base64_image = encode_image(image_path)

        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        }
                    ],
                }
            ])
        return response.choices[0].message.content
    except Exception as e:
        return str(e)


def ask_openai_llm(question, image_paths, prompt, model=DEFAULT_OPENAI):
    if not llm_client:
        return "OpenAI unavailable: OPENAI_API_KEY not set."
    full_paths = get_full_paths(image_paths)
    if not full_paths:
        return "Error: No images could be loaded. Please check the image paths."

    image_inputs = [encode_image(path) for path in full_paths]

    try:
        resp = llm_client.chat.completions.create(
            model=model,
            timeout=120,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        *[
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image}"
                                }
                            }
                            for image in image_inputs
                        ],
                        {"type": "text", "text": f"Question: {question}"}
                    ]
                }
            ]
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"⚠️ LLM request failed: {e}")
        return "LLM request failed: service temporarily unavailable or timed out"


def ask_openai_llm_html(question, html_paths, prompt, model=DEFAULT_OPENAI):
    if not llm_client:
        return "OpenAI unavailable: OPENAI_API_KEY not set."
    if not html_paths:
        return "Error: No HTML paths provided."

    html_pages = get_html_content(html_paths)

    # Build message content
    content_blocks = [{"type": "text", "text": prompt}]

    # Add each HTML page as its own text block
    for i, html in enumerate(html_pages, start=1):
        content_blocks.append({
            "type": "text",
            "text": f"HTML Page {i}:\n{html}"
        })

    # Add the question at the end
    content_blocks.append({
        "type": "text",
        "text": f"Question: {question}"
    })

    try:
        resp = llm_client.chat.completions.create(
            model=model,
            timeout=120,
            messages=[
                {
                    "role": "user",
                    "content": content_blocks
                }
            ]
        )
        # content = '\n'.join(content_blocks)
        # print("Prompt", content)
        return resp.choices[0].message.content

    except Exception as e:
        print(f"⚠️ LLM request failed: {e}")
        return "LLM request failed: service temporarily unavailable or timed out"
