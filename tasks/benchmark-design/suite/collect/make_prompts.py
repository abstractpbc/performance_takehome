"""Generate the seeded prompt sets from collect/corpus.md, cut at sentence breaks to fit token budgets."""

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

CORPUS = Path(__file__).with_name("corpus.md")
COUNTS = {"short": 1000, "long": 200, "long_gen": 200, "prefix": 200, "anchor": 150, "canary": 120}
PREFIX_QUESTION_TOKENS = 256

QUESTIONS = {
    "email": ("Draft a reply to the email above.",
              "Summarize what the email above is asking for, and what is still unclear.",
              "Rewrite the email above to be shorter and more direct."),
    "story": ("What is this story really about?",
              "Continue the story above for a few more paragraphs, in the same style.",
              "Describe the main character of the story above and how they change."),
    "explainer": ("Explain the passage above to a twelve-year-old.",
                  "Write three quiz questions, with answers, based on the passage above.",
                  "What is the most surprising fact in the passage above, and why?"),
    "code": ("Find the bugs in the code above and explain how to fix them.",
             "Explain what the code above does, step by step.",
             "Suggest tests for the code above."),
    "notes": ("Turn the notes above into a short status update for the wider team.",
              "List every action item in the notes above, with its owner.",
              "What risks do the notes above point to?"),
    "policy": ("Summarize the policy above as a short FAQ.",
               "Which parts of the policy above are most likely to cause confusion?",
               "Rewrite the policy above in plain language."),
    "incident": ("What was the root cause of the incident above, in one paragraph?",
                 "Which of the follow-up actions above matters most, and why?",
                 "Write a short message to customers about the incident above."),
    "docs": ("Using the documentation above, explain how to get started in three steps.",
             "What questions would a new user still have after reading the documentation above?",
             "Write a short example that uses what the documentation above describes."),
    "recipe": ("How could I make the recipe above vegan?",
               "Turn the recipe above into a shopping list.",
               "What can go wrong with the recipe above, and how do I avoid it?"),
    "essay": ("What is the main argument of the essay above, and is it convincing?",
              "Write a short counter-argument to the essay above.",
              "Summarize the essay above in three sentences."),
    "review": ("Is the reviewer above recommending it overall? Explain briefly.",
               "List the pros and cons from the review above.",
               "Write a one-paragraph summary of the review above for a busy reader."),
}
LONG_TASKS = (
    "Summarize each of the documents above in two sentences.",
    "Which of the documents above need action from someone this week? List them with the action.",
    "Group the documents above by theme and explain each group.",
    "Write a briefing for a new manager that covers the most important points in the documents above.",
    "List every number mentioned in the documents above, with what it refers to.",
)
GUIDE = ("Using the material above as a starting point, write a detailed guide with numbered "
         "sections and a worked example in each one.")
TRAINING = ("Write a thorough training document based on the material above, including common "
            "mistakes and how to avoid them.")
ARTICLE = "Expand the material above into a long article for a general audience, with headings."
STORY = "Write a long, detailed story inspired by the material above, with several scenes."
LONG_FORMS = {
    "email": (STORY, ARTICLE), "story": (STORY,), "explainer": (ARTICLE, GUIDE),
    "code": (GUIDE, TRAINING), "notes": (TRAINING, STORY), "policy": (TRAINING, ARTICLE),
    "incident": (TRAINING, GUIDE), "docs": (GUIDE, TRAINING), "recipe": (GUIDE, ARTICLE),
    "essay": (ARTICLE, STORY), "review": (ARTICLE, STORY),
}
SENTENCE_END = re.compile(r"[.!?]\s|\n")


def corpus() -> list[tuple[str, str, str]]:
    """(kind, title, text) for every `## kind | title` passage."""
    passages = []
    for chunk in CORPUS.read_text(encoding="utf-8").split("\n## ")[1:]:
        heading, _, text = chunk.partition("\n")
        kind, _, title = heading.partition(" | ")
        passages.append((kind.strip(), title.strip(), text.strip()))
    return passages


def size(tokenizer, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False).ids)


def fit(tokenizer, text: str, tokens: int) -> str:
    """Longest prefix of at most `tokens` tokens that ends at a sentence or paragraph break."""
    encoding = tokenizer.encode(text, add_special_tokens=False)
    if len(encoding.ids) <= tokens:
        return text
    cut = text[: encoding.offsets[tokens - 1][1]]
    ends = [match.end() for match in SENTENCE_END.finditer(cut)]
    cut = cut[: ends[-1]].rstrip() if ends else cut
    # Close a code fence the cut left open.
    return cut + "\n```" if cut.count("```") % 2 else cut


def single(tokenizer, passages, rng: random.Random, tokens: int, tails=QUESTIONS) -> str:
    kind, _, text = rng.choice(passages)
    tail = rng.choice(tails[kind])
    return f"{fit(tokenizer, text, tokens - size(tokenizer, tail) - 4)}\n\n{tail}"


def collection(tokenizer, passages, rng: random.Random, tokens: int) -> str:
    """Documents in a seeded order, the way a retrieval system pastes them into a prompt."""
    blocks = ["Here are some documents from our shared drive."]
    for number, (_, title, text) in enumerate(rng.sample(passages, len(passages)), 1):
        blocks.append(f"### Document {number}: {title}\n\n{text}")
        if size(tokenizer, "\n\n".join(blocks)) > tokens:
            break
    return fit(tokenizer, "\n\n".join(blocks), tokens)


def build(tokenizer, seed: int) -> dict[str, list[dict]]:
    passages = corpus()

    def chat(name: str, index: int, text: str, **extra) -> dict:
        return {"prompt_id": f"{name}-{index:04d}",
                "messages": [{"role": "user", "content": text}], **extra}

    sets: dict[str, list[dict]] = {}
    rng = random.Random(f"{seed}/short")
    sets["short"] = [chat("short", i, single(tokenizer, passages, rng, 256))
                     for i in range(COUNTS["short"])]
    rng = random.Random(f"{seed}/long")
    sets["long"] = []
    for i in range(COUNTS["long"]):
        task = rng.choice(LONG_TASKS)
        body = collection(tokenizer, passages, rng, 4096 - size(tokenizer, task) - 4)
        sets["long"].append(chat("long", i, f"{body}\n\n{task}"))
    rng = random.Random(f"{seed}/long_gen")
    sets["long_gen"] = [
        chat("long_gen", i, single(tokenizer, passages, rng, 128, LONG_FORMS))
        for i in range(COUNTS["long_gen"])
    ]
    rng = random.Random(f"{seed}/prefix")
    shared = collection(tokenizer, passages, rng, 4096 - PREFIX_QUESTION_TOKENS)
    sets["prefix"] = [
        chat("prefix", i, f"{shared}\n\nA colleague has also sent this:\n\n"
             + single(tokenizer, passages, rng, PREFIX_QUESTION_TOKENS - 12))
        for i in range(COUNTS["prefix"])
    ]
    rng = random.Random(f"{seed}/anchor")
    sets["anchor"] = [chat("anchor", i, single(tokenizer, passages, rng, 256))
                      for i in range(COUNTS["anchor"])]
    rng = random.Random(f"{seed}/canary")
    sets["canary"] = []
    for i in range(COUNTS["canary"]):
        left, right = rng.randint(100, 999), rng.randint(100, 999)
        text = f"Compute {left} + {right}. Reply with only the number. /no_think"
        sets["canary"].append(chat("canary", i, text, expected=str(left + right)))
    return sets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--seed", type=int, default=20261030)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(
        hf_hub_download(args.repository, "tokenizer.json", revision=args.revision)
    )
    args.out.mkdir(parents=True, exist_ok=True)
    for name, prompts in build(tokenizer, args.seed).items():
        data = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in prompts).encode()
        (args.out / f"{name}.jsonl").write_bytes(data)
        sizes = [
            len(tokenizer.encode(p["messages"][0]["content"], add_special_tokens=False).ids)
            for p in prompts
        ]
        print(json.dumps({
            "set": name, "prompts": len(prompts), "tokens_min": min(sizes),
            "tokens_max": max(sizes), "sha256": hashlib.sha256(data).hexdigest(),
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
