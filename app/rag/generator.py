"""
Grounded answer generation.

CONFIGURATION
-------------
Every runtime value comes from app.config.settings. This module
does not call load_dotenv() or os.getenv(): Settings already
reads .env, and having two places read the environment meant
GROQ_MODEL could differ between the ingestion stage and the
answer stage.

RELIABILITY
-----------
The Groq call goes through app.llm_client.GroqClient, which adds
retry with exponential backoff for transient failures (rate
limits, timeouts, 5xx) and fails fast on permanent ones (bad key,
unknown model). A failed call raises - it is never converted into
a confident-looking answer.
"""

import json
import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.config import settings
from app.llm_client import GroqClient
from app.rag.source_format import extract_source_identity, format_source_header
from app.confidence.models import Decision, ConfidenceResult


logger = logging.getLogger(__name__)


# ============================================================
# ANSWER GENERATOR
# ============================================================

class AnswerGenerator:
    """
    Generic grounded RAG answer generator.

    Works with technical documents such as:

    - BIS product manuals
    - Indian Standards
    - inspection documents
    - technical manuals
    - regulatory documents
    - procedures
    - specifications

    The generator is document-agnostic.

    It does NOT depend on a specific product,
    PDF, clause number, or document structure.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        client: Optional[GroqClient] = None
    ) -> None:
        """
        Parameters
        ----------
        model:
            Groq model id. Defaults to settings.GROQ_MODEL.

        temperature:
            Defaults to settings.GENERATION_TEMPERATURE.

        max_tokens:
            Defaults to settings.GENERATION_MAX_TOKENS.

        client:
            Preloaded GroqClient. Supplied by the API startup
            hook so one client is shared per process.

        Raises
        ------
        LLMUnavailableError
            If GROQ_API_KEY is not configured.
        """

        self.model = model or settings.GROQ_MODEL

        self.temperature = (
            settings.GENERATION_TEMPERATURE
            if temperature is None
            else temperature
        )

        self.max_tokens = (
            settings.GENERATION_MAX_TOKENS
            if max_tokens is None
            else max_tokens
        )

        # ----------------------------------------------------
        # CLIENT
        #
        # GroqClient raises LLMUnavailableError when the key is
        # missing, so a misconfigured deployment fails at
        # construction rather than on the first user query.
        # ----------------------------------------------------

        self.client = client if client is not None else GroqClient()

        logger.info(
            "Answer generator ready | model=%s | temperature=%.2f",
            self.model,
            self.temperature
        )

    # ========================================================
    # SAFE TEXT CONVERSION
    # ========================================================

    @staticmethod
    def _safe_text(
        value: Any
    ) -> str:
        """
        Safely convert a value into clean text.
        """

        if value is None:

            return ""

        if isinstance(
            value,
            str
        ):

            return value.strip()

        return str(
            value
        ).strip()

    # ========================================================
    # FORMAT CONTEXT
    # ========================================================

    def format_context(
        self,
        results: List[Dict[str, Any]]
    ) -> str:
        """
        Convert retrieved chunks into structured context.

        Minimum expected result:

        {
            "content": "..."
        }

        Optional fields (canonical index/retrieval names):

        {
            "chunk_id": "...",
            "metadata": {...},
            "document_id": "...",
            "section": "...",
            "standard_number": "...",
            "standard_title": "...",
            "clause_id": "...",
            "page_start": 1,
            "page_end": 1
        }
        """

        if not results:

            return ""

        formatted_chunks: List[str] = []

        for index, result in enumerate(
            results,
            start=1
        ):

            # ------------------------------------------------
            # VALIDATE RESULT
            # ------------------------------------------------

            if not isinstance(
                result,
                (dict, Mapping)
            ):

                logger.warning(
                    f"Skipping invalid result "
                    f"at position {index}"
                )

                continue

            # ------------------------------------------------
            # CONTENT (Authoritative source_content per Section 6)
            # ------------------------------------------------

            content = self._safe_text(
                result.get("source_content") or result.get("content", "")
            )

            if not content:

                continue

            # ------------------------------------------------
            # SOURCE PROVENANCE
            #
            # Delegated to app.rag.source_format so the grounding context
            # and the API `sources` array name a standard the same way.
            # ------------------------------------------------

            source_information = format_source_header(
                extract_source_identity(result)
            )

            # ------------------------------------------------
            # BUILD FORMATTED CHUNK WITH STABLE EVIDENCE TOKEN
            # ------------------------------------------------

            formatted_chunk = f"""
==================================================
RETRIEVED SOURCE {index} [EVIDENCE EV{index}]
==================================================

{source_information}

CONTENT:
{content}
"""

            formatted_chunks.append(
                formatted_chunk.strip()
            )

        return "\n\n".join(
            formatted_chunks
        )

    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    def get_system_prompt(
        self
    ) -> str:
        """
        Core grounding instructions.
        """

        return """
You are BIS-AI, a helpful assistant for understanding technical,
regulatory, inspection, testing, certification, and standards-related
documentation.

Your most important responsibility is ACCURACY.

You must answer using ONLY the retrieved documentation provided to you.

==================================================
ABSOLUTE GROUNDING RULE
==================================================

Never state something as a fact unless it is directly supported by the
retrieved documentation.

Do not use outside knowledge.

Do not guess.

Do not fill gaps using common sense.

Do not infer technical requirements that are not explicitly stated.

==================================================
CRITICAL RULE ABOUT CLAUSES
==================================================

A clause NUMBER is NOT the same as the CONTENT of that clause.

For example:

If the retrieved documentation says:

"Marking — Clause 7.5.8"

this only proves that Clause 7.5.8 is associated with marking.

It DOES NOT prove:

- what the full clause says
- what exact procedure must be followed
- what markings are required
- what equipment is required
- what records must be maintained

unless those details are explicitly present in the retrieved content.

Never invent the content of a clause merely because its number appears.

If the user asks:

"What is Clause 7.5.8?"

and the actual text of Clause 7.5.8 is NOT present, clearly say:

"The retrieved documentation identifies Clause 7.5.8 as related to
[topic], but the full text of the clause was not retrieved, so I cannot
explain its exact requirements from the available context."

==================================================
CRITICAL RULE ABOUT "WHAT SHOULD I DO?"
==================================================

Users may ask practical questions such as:

- What should I do?
- What is the next step?
- How do I perform this?
- What does this mean?

Only give an action or instruction if that action is explicitly
supported by the documentation.

DO NOT invent procedural steps.

For example, do not say:

"Perform the marking test"

unless the retrieved documentation actually provides that instruction.

Do not transform a table reference into a complete procedure unless
the procedure is present in the context.

==================================================
TECHNICAL TABLE INTERPRETATION
==================================================

Be extremely careful when interpreting tables.

A table may contain:

- clause references
- sample sizes
- frequencies
- symbols
- abbreviations
- equipment codes

Do not assume the meaning of a symbol or abbreviation unless the
retrieved documentation defines it.

For example:

If a table contains:

"R"

do NOT automatically claim that it means "Required" unless the
documentation explicitly defines R as Required.

==================================================
NUMERICAL INFORMATION
==================================================

Be extremely careful with:

- sample sizes
- percentages
- quantities
- tolerances
- dimensions
- temperatures
- time periods
- frequencies
- limits

Report numbers exactly as supported by the documentation.

Do not merge two different sampling requirements unless the
documentation clearly explains how they relate.

For example:

"10 pieces"

and

"1% of the batch, minimum 5"

may apply to different tests or situations.

Never present them as one universal rule unless the documentation
explicitly says so.

==================================================
CONFLICTING OR AMBIGUOUS INFORMATION
==================================================

If retrieved chunks appear to contain different requirements:

1. Do not silently merge them.
2. Explain that they appear to apply to different situations.
3. State what each requirement appears to refer to.
4. If the relationship cannot be determined, say so.

==================================================
HOW TO ANSWER
==================================================

Follow this order:

1. Give the direct answer first.

2. Explain what the documentation clearly states.

3. If the user asks what they should do, provide only actions directly
   supported by the documentation.

4. If important information is missing, clearly state what is missing.

5. When useful, explain technical language in simpler terms.

==================================================
USER-FRIENDLY EXPLANATION
==================================================

The user may not understand:

- clauses
- standards
- control units
- sampling
- technical symbols
- inspection terminology

Explain such terms in simple language ONLY when their meaning is
supported by the retrieved documentation.

If the document does not define a term, do not invent an official
definition.

You may say:

"The document uses the term 'control unit', but the exact definition
was not present in the retrieved context."

==================================================
WHAT NOT TO DO
==================================================

Never fabricate:

- clause contents
- standard requirements
- sample sizes
- testing procedures
- equipment requirements
- marking requirements
- certification requirements
- record keeping requirements
- dates
- product specifications
- regulatory requirements

Never add examples such as:

"manufacturer's name"

or

"calibration date"

unless those examples actually appear in the retrieved documentation.

==================================================
WHEN INFORMATION IS MISSING
==================================================

If the retrieved documentation does not provide enough information,
say clearly:

"I couldn't find the exact requirement in the retrieved
documentation."

Then explain only what IS available.

Example:

"The retrieved table links Clause 7.5.8 with marking, but the actual
text of Clause 7.5.8 was not included in the retrieved context.
Therefore, I cannot tell you the exact marking procedure."

This is better than guessing.

==================================================
STYLE
==================================================

Your answer should be:

- clear
- natural
- helpful
- technically accurate
- concise by default

Avoid robotic language.

Avoid unnecessary disclaimers.

Do not dump the entire source text.

==================================================
INTERNAL SYSTEM INFORMATION
==================================================

Never mention:

- embeddings
- vector databases
- ChromaDB
- BM25
- dense retrieval
- hybrid retrieval
- reranking
- cross encoders
- chunk IDs
- retrieval scores

==================================================
GENERATION SAFETY & NEGATIVE EVIDENCE RULES (Prompt 7)
==================================================

1. NEVER claim an AI result is "approved", "certified", or "officially compliant".
   AI interpretations are not official regulatory determinations.

2. NEVER infer legal currentness, validity, or supersession status unless
   explicitly stated in the retrieved text.

3. CRITICAL: Never convert the absence of evidence into negative evidence.
   If retrieved chunks do not mention a requirement, say:
   "No requirement was found in the retrieved documentation."
   NEVER say:
   "No requirement exists" or "The standard does not require this."

==================================================
CITATION ENFORCEMENT & STRUCTURED OUTPUT (Prompt 8)
==================================================

You must format your response as a valid JSON object matching this schema:

{
  "answer": "Clear, readable answer text. Include inline citation tags like [EV1], [EV2] at the end of every sentence or factual statement.",
  "claims": [
    {
      "claim_id": "C1",
      "text": "The exact factual, interpretive, or uncertainty claim statement",
      "claim_type": "fact",
      "citation_ids": ["EV1"]
    }
  ]
}

CITATION RULES:
1. Every retrieved passage is labelled with an explicit evidence token like [EVIDENCE EV1], [EVIDENCE EV2], etc.
2. Every factual statement MUST cite the exact evidence token supporting it (e.g. [EV1]).
3. NEVER invent citation tokens such as [EV99] or cite an evidence token that does not exist in the retrieved documentation.
4. NEVER invent page numbers, clause IDs, standard numbers, or amendment numbers.
5. NUMERICAL INTEGRITY: All numbers, percentages, measurements, frequencies, sample sizes, and tolerances must match the cited evidence text exactly. Do not round, approximate, or change units.
6. CLAIM TYPES:
   - "fact": statement directly supported and stated by cited evidence.
   - "interpretation": reasoned deduction or suggestion (e.g. "This suggests...").
   - "uncertainty": statement noting missing, incomplete, or unestablished requirements.
7. NEVER extrapolate regulatory conclusions such as "prohibited from selling" or "mandatory certification before sale" unless the retrieved text explicitly states it.
8. If evidence is missing, use an uncertainty claim (e.g. "The retrieved documentation does not specify X"). NEVER claim "X does not exist".

==================================================
TEMPORAL, VERSION & AMENDMENT SAFETY RULES (Prompt 9)
==================================================

1. NEVER call a document or edition "current" unless explicit source evidence in the retrieved text confirms it.
2. NEVER assume the latest publication date or highest year represents the currently active or legally binding standard.
3. NEVER declare a standard or edition "superseded" unless explicit supersession text (e.g., "supersedes IS XXXX") is present in the cited evidence.
4. NEVER consolidate or merge amendments into a base clause unless the retrieved source explicitly presents an official consolidated text. Always cite base text and amendment text separately.
5. When temporal currentness or supersession cannot be established from the retrieved evidence, EXPLICITLY state the uncertainty (e.g. "The current legal status could not be established from the retrieved documentation; verification is required.").

==================================================
FINAL ACCURACY CHECK
==================================================

Before answering, ask yourself:

1. Is every factual claim supported by the retrieved documentation?

2. Did I accidentally infer the content of a clause from its number?

3. Did I invent a procedure?

4. Did I assume the meaning of a symbol?

5. Did I combine different requirements incorrectly?

6. Did I directly answer the user's question?

If information is missing, say so instead of guessing.
"""

    # ========================================================
    # BUILD USER PROMPT
    # ========================================================

    def build_user_prompt(
        self,
        query: str,
        context: str,
        confidence: Optional[ConfidenceResult] = None,
        repair_feedback: Optional[str] = None,
    ) -> str:
        """
        Build the dynamic user prompt with confidence-aware and grounding instructions.
        """
        confidence_instruction = ""
        if confidence is not None:
            if confidence.decision == Decision.QUALIFIED_ANSWER:
                reasons_str = "; ".join(confidence.reasons) if confidence.reasons else "moderate evidence"
                confidence_instruction = f"""
==================================================
EVIDENCE QUALIFICATION INSTRUCTION
==================================================
The retrieved documentation provides moderate evidence confidence ({reasons_str}).
Explicitly state any limitations, assumptions, or unverified details in your response.
Clearly state what is directly confirmed in the text and what remains unverified.
"""
            elif confidence.decision == Decision.VERIFICATION_REQUIRED:
                unresolved_str = "; ".join(confidence.unresolved_aspects) if confidence.unresolved_aspects else "insufficient evidence"
                req_info_str = "; ".join(confidence.required_information) if confidence.required_information else "official standards"
                confidence_instruction = f"""
==================================================
CRITICAL ABSTENTION / VERIFICATION REQUIRED INSTRUCTION
==================================================
The system has determined that VERIFICATION IS REQUIRED:
Reason: {confidence.verification_reason}
Unresolved aspects: {unresolved_str}
Required information: {req_info_str}

Follow these instructions strictly:
1. Do NOT make confident factual assertions about unverified claims.
2. Clearly summarize only what is explicitly present in the retrieved passages.
3. Explicitly state that verification is required against official Indian Standards or competent authorities.
4. Highlight what specific documentation or standard version would need to be reviewed.
5. NEVER claim that a requirement does not exist merely because it is absent from the retrieved excerpts.
"""

        repair_instruction = ""
        if repair_feedback:
            repair_instruction = f"""
==================================================
GROUNDING CORRECTION REQUIRED
==================================================
The previous generation attempt produced the following grounding issues:
{repair_feedback}

Please regenerate your answer addressing these issues:
1. Remove or qualify any unsupported claims.
2. Only make factual assertions that are directly supported by the cited evidence.
3. Ensure every factual claim has an exact, valid evidence citation token (e.g. [EV1]).
"""

        return f"""
Answer the user's question using ONLY the retrieved documentation.

==================================================
USER QUESTION
==================================================

{query}

==================================================
RETRIEVED DOCUMENTATION
==================================================

{context}
{confidence_instruction}
{repair_instruction}
==================================================
IMPORTANT ANSWERING RULES
==================================================

- Answer directly using only facts supported by the retrieved documentation.
- Attach evidence citation tokens like [EV1], [EV2] to every factual assertion.
- Do not invent missing information.
- Format your entire output as a valid JSON object matching:
{{
  "answer": "Your complete readable answer text with inline citation tokens [EV1], etc.",
  "claims": [
    {{"claim_id": "C1", "text": "Claim statement", "claim_type": "fact", "citation_ids": ["EV1"]}}
  ]
}}

Now provide the final JSON response.
"""

    # ========================================================
    # EMPTY RESPONSE
    # ========================================================

    def _empty_result_response(
        self,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Response when no usable context exists.
        """
        msg = reason or (
            "Verification Required: I couldn't find relevant information in the "
            "available documentation to answer this question. Please verify against "
            "official Indian Standard publications."
        )
        return {
            "answer": msg,
            "raw_claims": [],
            "model": self.model,
            "context_chunks": 0,
        }

    # ========================================================
    # PARSE STRUCTURED OUTPUT
    # ========================================================

    @staticmethod
    def _parse_structured_output(text: str) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Parses structured JSON answer + claims from model response.
        Falls back gracefully if the model returned plain text or markdown.
        """
        stripped = text.strip()
        json_str = stripped
        if "```json" in stripped:
            start = stripped.find("```json") + 7
            end = stripped.find("```", start)
            json_str = stripped[start:end].strip() if end != -1 else stripped[start:].strip()
        elif "```" in stripped:
            start = stripped.find("```") + 3
            end = stripped.find("```", start)
            json_str = stripped[start:end].strip() if end != -1 else stripped[start:].strip()

        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict) and "answer" in parsed:
                answer = str(parsed["answer"]).strip()
                claims = parsed.get("claims", [])
                if isinstance(claims, list):
                    return answer, claims
                return answer, []
        except Exception:
            pass

        # Plain text fallback
        return stripped, []

    # ========================================================
    # GENERATE ANSWER
    # ========================================================

    def generate(
        self,
        query: str,
        results: List[Dict[str, Any]],
        confidence: Optional[ConfidenceResult] = None,
        repair_feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a grounded answer with evidence confidence awareness and grounding validation.
        """

        # ----------------------------------------------------
        # VALIDATE QUERY
        # ----------------------------------------------------

        if not isinstance(
            query,
            str
        ):

            raise TypeError(
                "Query must be a string."
            )

        query = query.strip()

        if not query:

            raise ValueError(
                "Query cannot be empty."
            )

        # ----------------------------------------------------
        # HANDLE EMPTY RESULTS
        # ----------------------------------------------------

        if not results:

            logger.warning(
                "No results provided to generator."
            )

            reason = confidence.verification_reason if confidence else None
            return self._empty_result_response(reason=reason)

        # ----------------------------------------------------
        # FORMAT CONTEXT
        # ----------------------------------------------------

        context = self.format_context(
            results
        )

        if not context.strip():

            logger.warning(
                "No usable context created."
            )

            reason = confidence.verification_reason if confidence else None
            return self._empty_result_response(reason=reason)

        logger.info(
            f"Generating answer using "
            f"{len(results)} retrieved results..."
        )

        # ----------------------------------------------------
        # BUILD PROMPTS
        # ----------------------------------------------------

        system_prompt = (
            self.get_system_prompt()
        )

        user_prompt = (
            self.build_user_prompt(
                query=query,
                context=context,
                confidence=confidence,
                repair_feedback=repair_feedback,
            )
        )

        # ----------------------------------------------------
        # CALL GROQ
        # ----------------------------------------------------

        response_text = self.client.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            model=self.model,
            temperature=self.temperature,
            max_completion_tokens=self.max_tokens,
            description="answer generation"
        ).strip()

        logger.info("Answer generated successfully.")

        answer, raw_claims = self._parse_structured_output(response_text)

        # ----------------------------------------------------
        # RETURN RESULT
        # ----------------------------------------------------

        return {
            "answer": answer,
            "raw_claims": raw_claims,
            "model": self.model,
            "context_chunks": len(results),
        }