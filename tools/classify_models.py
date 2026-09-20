from __future__ import annotations


SKIP_SUBSTR = ("embed", "-vl:", "vl:", "vl30", "vl8")
SKIP_SIZE = ("30b", "31b", "32b", "27b", "a3b")

WRAPPER_MARKS = (
    "mvp64k",
    "t3ctx",
    "syswrap",
    "codebridge",
    "-ft-",
    "finetuned",
    "-test",
    "q8-test",
)


def kind_of(name: str) -> str:
    n = name.lower()
    if "embed" in n:
        return "skip-embed"
    if any(s in n for s in ("-vl:", "vl:", "qwen3vl")):
        return "skip-vision"
    if any(s in n for s in SKIP_SIZE) and "nano:4b" not in n:
        return "skip-heavy"
    if any(m in n for m in ("-ft-", "finetuned")):
        return "finetune"
    if any(m in n for m in WRAPPER_MARKS):
        return "wrapper"
    return "native"


def family_of(name: str) -> str:
    n = name.lower().replace("_", "-")
    keys = (
        "gemma4",
        "qwen3-8b",
        "qwen3:8b",
        "qwen3:4b",
        "llama3.1",
        "llama31",
        "hermes3",
        "hermes4",
        "cogito14",
        "cogito:14",
        "cogito32",
        "cogito:32",
        "glm47",
        "glm-4.7",
        "granite4:tiny",
        "granite4:small",
        "granite4:micro",
        "granite4micro",
        "granite4-small",
        "lfm2",
        "lfm25",
        "devstral-small-2",
        "devstral-codebridge",
        "apriel",
        "mellum",
        "nemotron-3-nano:4b",
        "nemotron35",
        "laguna",
        "ornith",
        "gpt-oss",
    )
    for k in keys:
        if k in n:
            return k
    return name.split(":")[0][:24]
