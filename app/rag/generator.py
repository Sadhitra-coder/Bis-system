"""
Grounded answer generation.

CONFIGURATION
-------------
Every runtime value comes from app.config.settings. This module
does not call load_dotenv() or os.getenv(): Settings already
reads .env, and having two places read the environment meant
OPENAI_MODEL could differ between the ingestion stage and the
answer stage.

RELIABILITY
-----------
The OpenAI call goes through app.llm_client.OpenAIClient, which adds
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
from app.llm_client import OpenAIClient
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
        client: Optional[OpenAIClient] = None
    ) -> None:
        """
        Parameters
        ----------
        model:
            OpenAI model id. Defaults to settings.OPENAI_MODEL.

        temperature:
            Defaults to settings.GENERATION_TEMPERATURE.

        max_tokens:
            Defaults to settings.GENERATION_MAX_TOKENS.

        client:
            Preloaded OpenAIClient. Supplied by the API startup
            hook so one client is shared per process.

        Raises
        ------
        LLMUnavailableError
            If OPENAI_API_KEY is not configured.
        """

        self.model = model or settings.OPENAI_MODEL

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
        # OpenAIClient raises LLMUnavailableError when the key is
        # missing, so a misconfigured deployment fails at
        # construction rather than on the first user query.
        # ----------------------------------------------------

        self.client = client if client is not None else OpenAIClient()

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
        self,
        audience: str = "technical",
    ) -> str:
        """
        Customer-readable and grounding system prompt supporting both technical and consumer audiences.
        Enforces clear visual hierarchy: bold headline, plain explanation, structured bullets, separated sources.
        """
        mode = (audience or "technical").lower().strip()
        if mode == "consumer":
            audience_block = """
==================================================
AUDIENCE: CONSUMER / SIMPLE EXPLANATION MODE
==================================================
Your target audience is a general consumer or product buyer:
1. Use clear, simple, everyday language without technical jargon.
2. OMIT all clause numbers and section references from the response body (do NOT mention "Clause 4.1", "Clause 18", etc.).
3. Clearly explain what the product is, key safety and quality features, and what protection the standard ensures.
4. Conclude with this exact sentence as a separate line before Sources:
   "Consumers can verify the authenticity of the ISI mark or license validity using the official BIS Care mobile app."
   (Do NOT attach citation tags to this BIS Care app tip, and do NOT include it as a factual claim in the "claims" list.)
5. Maintain strict grounding fidelity to the cited passages without inventing unsupported generalizations.
"""
        else:
            audience_block = """
==================================================
AUDIENCE: REGULATORY & TECHNICAL COMPLIANCE MODE
==================================================
Your target audience is a compliance manager, testing engineer, or regulatory officer:
1. Provide precise technical specifications, test methods, tolerances, and explicit clause numbers in the structured bullet points.
2. Maintain formal engineering and regulatory precision.
"""

        return f"""You are BIS-AI, an expert compliance assistant for Indian Standards published by the Bureau of Indian Standards (BIS).

Your most vital responsibility is FACTUAL ACCURACY and GROUNDEDNESS.
You must answer using ONLY the retrieved documentation provided in the prompt context.

==================================================
ABSOLUTE GROUNDING & NO-HALLUCINATION RULES
==================================================
1. Never state something as a fact unless it is directly supported by the retrieved documentation.
2. Do not use outside knowledge, extrapolate, or guess.
3. Every factual claim MUST be directly supported by the retrieved evidence. Associate each claim with its supporting evidence token in the "claims" list (e.g. "citation_ids": ["EV1"]).
4. NEVER invent citation tokens such as [EV99] or cite non-existent evidence tokens.
5. NUMERICAL INTEGRITY: All numbers, percentages, measurements, frequencies, sample sizes, and tolerances must match the cited evidence text exactly. Do not round, approximate, or change units.
6. A clause number is NOT the same as its text. Never invent the content of a clause merely because its number was mentioned.
7. CRITICAL: Never convert the absence of evidence into negative evidence. If retrieved passages do not mention a requirement, say "No requirement was found in the retrieved documentation." Never claim a requirement does not exist.

==================================================
ANSWER STRUCTURE & VISUAL HIERARCHY (REQUIRED)
==================================================
Structure your answer in clean markdown with clear visual hierarchy following this exact 4-part structure:

1. BOLD HEADLINE / STATUS LINE:
   Start with a short, bolded headline or status line:
   - For version/currentness queries: e.g. "**Status: Currently in force**" or "**Status: Verification Required (Unconfirmed Currentness)**".
   - For factual or technical queries: a short bolded direct answer (e.g. "**IS 1786 specifies Fe 500D grade rebar requirements.**").

2. PLAIN EXPLANATION:
   Follow with 2 to 4 sentences of clear, plain-language explanation providing the direct answer, standard scope, and essential context.

3. STRUCTURED DETAILS (CLEAN SHORT BULLETS OR NUMBERED LIST):
   Where there are multiple distinct facts (such as technical specifications, test methods, version timeline, or amendments), format them as a clean, short bulleted or numbered list. NEVER cram multiple distinct requirements or timeline events into a single dense paragraph.
   - For version queries: list the current edition/year, superseded edition details, and published amendments.
   - For technical queries: list each key requirement, test method, or parameter.

4. SEPARATED CITATION SOURCES LINE AT THE VERY END:
   Place citations as a clearly separated "Sources:" line at the very end of your response, NOT scattered inline as [EV1][EV2] mid-sentence.
   Format:
   Sources: [EV1], [EV2]
   (or "Sources: [EV1]" if single source)

5. ELIMINATE META-PHRASING:
   Never use bureaucratic meta-phrasing such as:
   - "The retrieved documentation does not specify..."
   - "The retrieved table links Clause..."
   - "Based on the provided excerpts..."
   Instead, state directly what the standard establishes and requires.

{audience_block}

==================================================
JSON OUTPUT SCHEMA
==================================================
You must format your entire response as a valid JSON object matching this schema:
{{
  "answer": "**Status: Currently in force**\\n\\nIS 1293 is active and in force as the Fourth Revision published in 2019. It establishes safety and design requirements for plugs and socket-outlets for domestic use.\\n\\n- Edition: Fourth Revision (2019), effective December 1, 2019\\n- Supersedes: IS 1293:2005 (Third Revision), withdrawn October 23, 2020\\n- Amendments: Amendment No. 1 (effective Dec 2020) and Amendment No. 2 (effective Sept 25, 2023)\\n\\nSources: [EV1], [EV2]",
  "claims": [
    {{
      "claim_id": "C1",
      "text": "Exact factual claim directly supported by cited evidence",
      "claim_type": "fact",
      "citation_ids": ["EV1"]
    }}
  ]
}}
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
        audience: str = "technical",
        temporal_resolution: Optional[Any] = None,
    ) -> str:
        """
        Build the dynamic user prompt with confidence-aware, audience-aware, and visual hierarchy instructions.
        """
        mode = (audience or "technical").lower().strip()
        audience_instruction = ""
        if mode == "consumer":
            audience_instruction = """
- Target Audience: Consumer. Use clear everyday language, omit clause numbers from the answer, and conclude with the official BIS Care mobile app verification tip (without citation tags) immediately before the Sources line.
"""
        else:
            audience_instruction = """
- Target Audience: Technical. Include exact clause numbers, testing parameters, and regulatory references in the bullet points.
"""

        temporal_instruction = ""
        if temporal_resolution is not None:
            t_status = getattr(temporal_resolution, "status", None)
            t_status_val = getattr(t_status, "value", str(t_status)) if t_status else ""
            t_req = getattr(temporal_resolution, "requires_verification", False)
            t_reason = getattr(temporal_resolution, "reason", "")
            if t_status_val == "current_supported" and not t_req:
                temporal_instruction = f"""
==================================================
VERIFIED TEMPORAL / VERSION RECORD
==================================================
The standard's temporal status is VERIFIED in the official BIS Registry:
- Status: Legal Currentness Confirmed ({t_status_val})
- Details: {t_reason}
Use the bold headline: "**Status: Currently in force**"
In the explanation and bulleted timeline, explicitly confirm the current active edition/year, prior superseded edition, and published amendments as supported by the retrieved evidence.
"""
            elif t_req or t_status_val == "temporally_uncertain":
                temporal_instruction = f"""
==================================================
TEMPORAL UNCERTAINTY NOTICE
==================================================
The legal currentness of this standard could NOT be confirmed from official BIS supersession records:
- Reason: {t_reason}
- Requirement: State clearly that legal currentness is unverified in the active index and must be confirmed via official BIS gazette or the BIS portal (services.bis.gov.in).
Do NOT assert that this standard is currently legally binding. Clearly explain the uncertainty.
Use the bold headline: "**Status: Verification Required (Unconfirmed Currentness)**"
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
{temporal_instruction}
{repair_instruction}
==================================================
REQUIRED ANSWER STRUCTURE & FORMAT
==================================================

- Follow this exact 4-part visual hierarchy:
  1. A short bolded headline/status line (e.g. "**Status: Currently in force**" or a direct bold answer).
  2. 2 to 4 sentences of plain explanation.
  3. A clean short bullet or numbered list for multiple distinct facts, timeline events, or requirements.
  4. Citations ONLY in a clearly separated "Sources: [EV1], [EV2]" line at the very end, NOT scattered inline mid-sentence.
- Associate each factual claim in the "claims" list with its supporting evidence token (e.g. "citation_ids": ["EV1"]).
- Do not invent missing information.
{audience_instruction}
- Format your entire output as a valid JSON object matching:
{{
  "answer": "Your complete formatted answer in markdown with bold headline, explanation, clean bullets, and separated Sources: [EV1], [EV2] line at end.",
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
        audience: str = "technical",
        temporal_resolution: Optional[Any] = None,
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
            f"{len(results)} retrieved results (audience={audience})..."
        )

        # ----------------------------------------------------
        # BUILD PROMPTS
        # ----------------------------------------------------

        system_prompt = (
            self.get_system_prompt(audience=audience)
        )

        user_prompt = (
            self.build_user_prompt(
                query=query,
                context=context,
                confidence=confidence,
                repair_feedback=repair_feedback,
                audience=audience,
                temporal_resolution=temporal_resolution,
            )
        )

        # ----------------------------------------------------
        # CALL OPENAI
        # ----------------------------------------------------

        try:
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
        except Exception as error:
            logger.error("OpenAI answer generation call failed: %s", error, exc_info=True)
            raise

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