from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from core.rag_engine import get_llm, get_llm_json


def extract_meeting_insights(transcript: str) -> dict:
    if not transcript or not transcript.strip():
        return {
            "title": "Untitled Meeting",
            "summary": "No speech detected in this recording.",
            "action_items": [],
            "key_decisions": [],
            "open_questions": []
        }

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are an expert meeting and video analyst.

Analyze the transcript and return ONLY valid JSON in the following format:

{{
  "title": "short meeting title",
  "summary": "detailed markdown summary",
  "action_items": ["item1", "item2"],
  "key_decisions": ["decision1", "decision2"],
  "open_questions": ["question1", "question2"]
}}

Instructions:
1. "title": A concise, descriptive title (maximum 8 words).
2. "summary": A comprehensive, high-value summary of the transcript formatted in markdown. Include an Executive Summary, Detailed Discussion by topic, and Key Takeaways.
3. "action_items": A list of concrete tasks, commands, or next steps mentioned. Return [] if none.
4. "key_decisions": A list of important conclusions, decisions, or core takeaways. Return [] if none.
5. "open_questions": A list of unresolved questions, concerns, or blockers. Return [] if none.

Return valid JSON only. Do not wrap in markdown code blocks like ```json. Do not include any introductory or concluding text outside the JSON object."""
        ),
        ("human", "Transcript:\n\n{transcript}")
    ])

    # Truncate very long transcripts to ~15,000 chars to avoid token limits
    transcript_prompt = transcript[:15000] if len(transcript) > 15000 else transcript

    parser = JsonOutputParser()
    result = None

    # Try primary structured JSON mode
    try:
        llm = get_llm_json()
        chain = prompt | llm | parser
        result = chain.invoke({"transcript": transcript_prompt})
    except Exception as err:
        print(f"Primary JSON-mode failed ({err}), falling back to gpt-oss-120b...")
        try:
            fallback_llm = get_llm_json(model="openai/gpt-oss-120b")
            chain = prompt | fallback_llm | parser
            result = chain.invoke({"transcript": transcript_prompt})
        except Exception as err2:
            print(f"Secondary JSON fallback failed ({err2}), trying text parser with gpt-oss-20b...")
            try:
                fallback_llm = get_llm(model="openai/gpt-oss-20b")
                chain = prompt | fallback_llm | parser
                result = chain.invoke({"transcript": transcript_prompt})
            except Exception as final_err:
                print(f"All extraction attempts failed: {final_err}")
                result = {
                    "title": "Meeting Summary",
                    "summary": f"Could not generate automated summary due to rate limits. Transcript ({len(transcript)} chars) is indexed for chat.",
                    "action_items": [],
                    "key_decisions": [],
                    "open_questions": []
                }

    # Ensure required keys exist
    clean_result = {
        "title": result.get("title") or "Meeting Summary",
        "summary": result.get("summary") or "No summary available.",
        "action_items": result.get("action_items") if isinstance(result.get("action_items"), list) else [],
        "key_decisions": result.get("key_decisions") if isinstance(result.get("key_decisions"), list) else [],
        "open_questions": result.get("open_questions") if isinstance(result.get("open_questions"), list) else [],
    }

    # Repair any escaped newlines
    if isinstance(clean_result["summary"], str):
        clean_result["summary"] = clean_result["summary"].replace("\\n", "\n")

    return clean_result