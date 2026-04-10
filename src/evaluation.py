from textwrap import dedent

from prompt1 import TEAM_PROFILE

EVALUATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "paper_evaluation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "relevance_score": {"type": "integer"},
                "summary":         {"type": "string"},
                "key_insight":     {"type": "string"},
            },
            "required": ["relevance_score", "summary", "key_insight"],
            "additionalProperties": False,
        },
    },
}


def build_paper_evaluation_prompt(paper):
    """Build a stable prompt for LLM paper relevance scoring."""
    interests = "\n".join(f"- {interest}" for interest in TEAM_PROFILE["interests"])
    avoid_topics = "\n".join(f"- {avoid}" for avoid in TEAM_PROFILE["avoid"])

    return dedent(
        f"""\
        You are curating research papers for the IFoA General Insurance Machine Learning in Reserving Working Party.

        Judge papers by practical usefulness for actuarial claims reserving research and practice, not by generic machine learning novelty.

        Scoring rubric:
        - 9 to 10: directly about reserving and highly useful to the working party
        - 7 to 8: not directly about reserving, but strongly transferable with clear practical value
        - 5 to 6: adjacent and somewhat useful, but not a priority
        - 0 to 4: weak relevance to reserving or little practical value

        TEAM PROFILE:
        Focus: {TEAM_PROFILE["focus"]}

        What they care about:
        {interests}

        What to avoid:
        {avoid_topics}

        Evaluate this research paper.

        TITLE:
        {paper["title"]}

        ABSTRACT:
        {paper["abstract"]}

        INSTRUCTIONS:
        1. relevance_score: output an integer from 0 to 10.
        2. summary: always write a 1-2 sentence summary. Never return null or skip this field.
        3. key_insight: write exactly one sentence stating the main takeaway.

        Respond ONLY with valid JSON in this format:
        {{
          "relevance_score": 0,
          "summary": null,
          "key_insight": "string"
        }}
        """
    )


def create_batch_evaluation(papers, model_name, log_fn=None):
    """Create batch requests for OpenAI that are safe for gpt-4o-mini."""

    requests = []
    for idx, paper in enumerate(papers, 1):
        prompt = build_paper_evaluation_prompt(paper)
        if log_fn:
            log_fn(
                "[dw_queue] request=%s/%s paper_id=%s title=%s"
                % (idx, len(papers), paper["id"], paper["title"])
            )

        requests.append({
            "custom_id": paper["id"],
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model_name,
                "response_format": EVALUATION_SCHEMA,
                "temperature": 0,
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a model that must output ONLY valid JSON. No explanations. No extra text."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            }
        })

    return requests
