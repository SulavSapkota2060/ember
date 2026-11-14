"""One place to build chat models. Claude 5.x models think by default (adaptive thinking), which adds hidden output tokens
and seconds to every call. Measured on Haiku 5.5 for the interior vision pass: 5.5s with default thinking (628 reasoning
tokens) vs 1.8s with thinking disabled, same JSON. We turn it off everywhere unless a call asks for it."""
from langchain.chat_models import init_chat_model


def chat(model: str, think: bool = False):
    """Haiku: thinking disabled. Sonnet 5.5 cannot disable thinking (the API rejects it) but its adaptive thinking
    stayed at 0 reasoning tokens on a simple call, so we only lower its effort. Output length is its real latency lever."""
    if think:  # Deep mode: ask the API to return a SUMMARY of the model's reasoning (the default returns empty thinking text)
        return init_chat_model(model, thinking={"type": "adaptive", "display": "summarized"})
    if "haiku" in model:
        return init_chat_model(model, thinking={"type": "disabled"})
    return init_chat_model(model, reasoning_effort="low")
