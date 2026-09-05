from __future__ import annotations

import json
import re

from groq import Groq

from app.config import settings
from app.schemas.queries import QueryUnderstanding


client = Groq(
    api_key=settings.GROQ_API_KEY
)


SYSTEM_PROMPT = """
You are the query-understanding component
of a BIS document retrieval system.

Your job is to convert a user's natural-language
query into structured metadata.

Return ONLY valid JSON.

Allowed intents:

- search_documents
- find_standard
- find_amendment
- find_guideline
- find_form
- find_scheme
- find_circular
- find_latest
- general_information

Allowed document types:

- Indian Standard
- Amendment
- Circular
- Form
- Guideline
- Scheme
- Gazette Notification
- Application
- Annual Report
- Notification
- Brochure
- Organisation Document
- Manual
- Document

Allowed categories:

- Standards
- Laboratory Services
- Schemes
- Hallmarking
- Training
- International
- Consumer Affairs
- Product Certification
- General

IMPORTANT STANDARD NUMBER RULE:

If the user says:

IS 456

return:

"standard_number": "IS 456"

If the user says:

IS 456:2000

return:

"standard_number": "IS 456:2000"

If the user says:

Indian Standard 10262

return:

"standard_number": "IS 10262"

Do NOT return only:

"456"

Do NOT infer a standard number from an arbitrary number.

For example:

"PM 4566"

does NOT necessarily mean:

"IS 4566"

If the user asks for the newest,
latest, current, or most recent document:

needs_latest = true

Otherwise:

needs_latest = false

needs_latest MUST ALWAYS be boolean.

limit MUST ALWAYS be an integer from 1 to 50.

If a field is unknown, use null.

Return exactly:

{
    "intent": "search_documents",
    "search_text": null,
    "document_type": null,
    "category": null,
    "subcategory": null,
    "standard_number": null,
    "language": null,
    "needs_latest": false,
    "limit": 10
}
"""


# ============================================================
# JSON PARSER
# ============================================================

def _parse_json(text: str) -> dict:

    if not text:
        raise ValueError(
            "LLM returned an empty response"
        )

    text = text.strip()

    # Remove markdown code fences if present.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:
        return json.loads(text)

    except json.JSONDecodeError:

        # Fallback: locate the outermost JSON object.
        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError(
                "LLM did not return valid JSON"
            )

        try:
            return json.loads(
                text[start:end + 1]
            )

        except json.JSONDecodeError as exc:
            raise ValueError(
                "LLM returned malformed JSON"
            ) from exc


# ============================================================
# RESPONSE NORMALIZATION
# ============================================================

def _normalize_response(
    data: dict,
) -> dict:

    string_fields = [
        "intent",
        "search_text",
        "document_type",
        "category",
        "subcategory",
        "standard_number",
        "language",
    ]

    for field in string_fields:

        value = data.get(field)

        if value is not None:

            value = str(value).strip()

            if value:
                data[field] = value
            else:
                data[field] = None

    # --------------------------------------------------------
    # Intent
    # --------------------------------------------------------

    data["intent"] = (
        data.get("intent")
        or "search_documents"
    )

    # --------------------------------------------------------
    # Latest
    # --------------------------------------------------------

    value = data.get(
        "needs_latest",
        False,
    )

    if isinstance(value, bool):

        data["needs_latest"] = value

    elif isinstance(value, str):

        data["needs_latest"] = (
            value.strip().lower()
            in {
                "true",
                "yes",
                "1",
            }
        )

    else:

        data["needs_latest"] = False

    # --------------------------------------------------------
    # Limit
    # --------------------------------------------------------

    try:

        limit = int(
            data.get(
                "limit",
                10,
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        limit = 10

    data["limit"] = max(
        1,
        min(limit, 50),
    )

    return data


# ============================================================
# PUBLIC FUNCTION
# ============================================================

def understand_query(
    user_query: str,
) -> QueryUnderstanding:

    if not user_query or not user_query.strip():

        raise ValueError(
            "Query cannot be empty"
        )

    response = client.chat.completions.create(

        model=settings.GROQ_MODEL,

        temperature=0,

        response_format={
            "type": "json_object"
        },

        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_query.strip(),
            },
        ],
    )

    raw = (
        response
        .choices[0]
        .message
        .content
    )

    data = _parse_json(raw)

    data = _normalize_response(
        data
    )

    return QueryUnderstanding.model_validate(
        data
    )