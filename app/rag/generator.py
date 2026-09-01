import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

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
        temperature: float = 0.1,
        max_tokens: int = 1200
    ) -> None:

        print("\n" + "=" * 70)
        print("INITIALIZING ANSWER GENERATOR")
        print("=" * 70 + "\n")

        # ----------------------------------------------------
        # MODEL CONFIGURATION
        # ----------------------------------------------------

        self.model = (
            model
            or os.getenv(
                "GROQ_MODEL",
                "openai/gpt-oss-20b"
            )
        )

        self.temperature = temperature
        self.max_tokens = max_tokens

        # ----------------------------------------------------
        # API KEY
        # ----------------------------------------------------

        api_key = os.getenv(
            "GROQ_API_KEY"
        )

        if not api_key:

            raise ValueError(
                "\nGROQ_API_KEY not found.\n\n"
                "Add the following to your .env file:\n\n"
                "GROQ_API_KEY=your_api_key_here\n"
            )

        # ----------------------------------------------------
        # INITIALIZE CLIENT
        # ----------------------------------------------------

        logger.info(
            "Connecting to Groq..."
        )

        self.client = Groq(
            api_key=api_key
        )

        logger.info(
            f"Model selected: {self.model}"
        )

        print("\n" + "=" * 70)
        print("ANSWER GENERATOR READY")
        print("=" * 70)

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

        Optional fields:

        {
            "chunk_id": "...",
            "metadata": {...},
            "document_id": "...",
            "section": "...",
            "title": "...",
            "standard": "..."
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
                dict
            ):

                logger.warning(
                    f"Skipping invalid result "
                    f"at position {index}"
                )

                continue

            # ------------------------------------------------
            # CONTENT
            # ------------------------------------------------

            content = self._safe_text(
                result.get(
                    "content",
                    ""
                )
            )

            if not content:

                continue

            # ------------------------------------------------
            # METADATA
            # ------------------------------------------------

            metadata = result.get(
                "metadata",
                {}
            )

            if not isinstance(
                metadata,
                dict
            ):

                metadata = {}

            # ------------------------------------------------
            # EXTRACT OPTIONAL SOURCE INFORMATION
            # ------------------------------------------------

            document_id = self._safe_text(
                metadata.get(
                    "document_id"
                )
                or result.get(
                    "document_id"
                )
            )

            section = self._safe_text(
                metadata.get(
                    "section"
                )
                or result.get(
                    "section"
                )
            )

            title = self._safe_text(
                metadata.get(
                    "title"
                )
                or result.get(
                    "title"
                )
            )

            standard = self._safe_text(
                metadata.get(
                    "standard"
                )
                or result.get(
                    "standard"
                )
            )

            source_file = self._safe_text(
                metadata.get(
                    "source_file"
                )
                or result.get(
                    "source_file"
                )
            )

            # ------------------------------------------------
            # SOURCE HEADER
            # ------------------------------------------------

            source_lines: List[str] = []

            if document_id:

                source_lines.append(
                    f"Document: {document_id}"
                )

            if title:

                source_lines.append(
                    f"Title: {title}"
                )

            if standard:

                source_lines.append(
                    f"Standard: {standard}"
                )

            if section:

                source_lines.append(
                    f"Section: {section}"
                )

            if source_file:

                source_lines.append(
                    f"Source file: {source_file}"
                )

            if source_lines:

                source_information = "\n".join(
                    source_lines
                )

            else:

                source_information = (
                    "Source information not available."
                )

            # ------------------------------------------------
            # BUILD FORMATTED CHUNK
            # ------------------------------------------------

            formatted_chunk = f"""
==================================================
RETRIEVED SOURCE {index}
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
        context: str
    ) -> str:
        """
        Build the dynamic user prompt.
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

==================================================
IMPORTANT ANSWERING RULES
==================================================

- Answer directly.
- Use only information supported by the retrieved documentation.
- Do not invent missing information.
- Do not infer the full content of a clause from its clause number.
- Do not invent procedural steps.
- Do not assume the meaning of abbreviations or table symbols unless
  they are explicitly defined.
- Keep different requirements separate when they may apply to different
  situations.
- If the exact answer is missing, clearly say what the documentation
  does provide and what is missing.
- Explain technical information in simple language where possible.
- Do not mention internal retrieval systems.

Now provide the final answer.
"""

    # ========================================================
    # EMPTY RESPONSE
    # ========================================================

    def _empty_result_response(
        self
    ) -> Dict[str, Any]:
        """
        Response when no usable context exists.
        """

        return {
            "answer": (
                "I couldn't find relevant information in the "
                "available documentation to answer this question."
            ),

            "model":
                self.model,

            "context_chunks":
                0
        }

    # ========================================================
    # GENERATE ANSWER
    # ========================================================

    def generate(
        self,
        query: str,
        results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate a grounded answer.
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

            return self._empty_result_response()

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

            return self._empty_result_response()

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
                context=context
            )
        )

        # ----------------------------------------------------
        # CALL GROQ
        # ----------------------------------------------------

        try:

            completion = (
                self.client
                .chat
                .completions
                .create(
                    model=self.model,

                    messages=[
                        {
                            "role": "system",
                            "content":
                                system_prompt
                        },
                        {
                            "role": "user",
                            "content":
                                user_prompt
                        }
                    ],

                    temperature=
                        self.temperature,

                    max_completion_tokens=
                        self.max_tokens,

                    top_p=0.9
                )
            )

        except Exception as error:

            logger.exception(
                "Groq generation failed."
            )

            raise RuntimeError(
                "Failed to generate an answer."
            ) from error

        # ----------------------------------------------------
        # EXTRACT ANSWER
        # ----------------------------------------------------

        answer = ""

        try:

            if completion.choices:

                message = (
                    completion
                    .choices[0]
                    .message
                )

                answer = (
                    message.content
                    or ""
                ).strip()

        except Exception:

            logger.exception(
                "Failed to extract answer."
            )

        # ----------------------------------------------------
        # FALLBACK
        # ----------------------------------------------------

        if not answer:

            answer = (
                "I couldn't generate a clear answer from "
                "the retrieved documentation."
            )

        logger.info(
            "Answer generated successfully."
        )

        # ----------------------------------------------------
        # RETURN RESULT
        # ----------------------------------------------------

        return {
            "answer":
                answer,

            "model":
                self.model,

            "context_chunks":
                len(results)
        }