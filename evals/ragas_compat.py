"""Import this module BEFORE ragas, anywhere in the eval harness.

ragas 0.4.3 does `from langchain_community.chat_models.vertexai import
ChatVertexAI` at import time, but langchain-community 0.4 removed that
submodule, so `import ragas` crashes (ModuleNotFoundError, found
2026-08-12). ragas only uses the symbol in an isinstance allowlist
(MULTIPLE_COMPLETION_SUPPORTED in ragas/llms/base.py), so a dummy class
keeps that check correct: our Gemini wrapper is not a ChatVertexAI and
isinstance stays False. Remove this module when a ragas release imports
cleanly against langchain-community 0.4+.
"""

import sys
import types


def install() -> None:
    name = "langchain_community.chat_models.vertexai"
    try:
        __import__(name)
        return
    except ImportError:
        pass

    module = types.ModuleType(name)

    class ChatVertexAI:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Stub from evals/ragas_compat.py; not a real ChatVertexAI."
            )

    module.ChatVertexAI = ChatVertexAI
    sys.modules[name] = module
    import langchain_community.chat_models as parent

    parent.vertexai = module


install()
