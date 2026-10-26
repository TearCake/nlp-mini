from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from core.rag_engine import get_llm, get_llm_json


def get_format_instruction(output_format: str = "translated") -> str:
    fmt = (output_format or "translated").lower().strip()
    if fmt in ["hindi", "hindi_font", "devanagari"]:
        return (
            "6. LANGUAGE REQUIREMENT: Output all fields (title, summary, action_items, key_decisions, open_questions) "
            "strictly in natural Hindi using Devanagari script (हिन्दी फ़ॉन्ट). "
            "Retain essential technical terms (API, Git, Cloud, etc.) in standard phonetic or English script."
        )
    elif fmt in ["romanized", "roman_transliteration", "hinglish"]:
        return (
            "6. LANGUAGE REQUIREMENT: Output all fields (title, summary, action_items, key_decisions, open_questions) "
            "strictly in natural conversational Hinglish using Roman / Latin script (e.g. 'Is video me API ke baare me explain kiya gaya hai...'). "
            "Do NOT use Devanagari script."
        )
    else:
        return (
            "6. LANGUAGE REQUIREMENT: Output all fields in clear, professional English."
        )


def convert_transcript_chunk(chunk_text: str, target_format: str) -> str:
    fmt = (target_format or "translated").lower().strip()
    if fmt == "translated":
        inst = "Translate all spoken dialogue into clear, natural English. Keep all technical terms accurate."
    elif fmt in ["hindi", "hindi_font", "devanagari"]:
        inst = "Translate/transcribe all spoken dialogue into natural Hindi using Devanagari script (हिन्दी फ़ॉन्ट). Retain common technical terms (API, code, server, etc.) phonetically or in English."
    elif fmt in ["romanized", "roman_transliteration", "hinglish"]:
        inst = "Translate/transcribe all spoken dialogue into natural conversational Hinglish using Roman / Latin script (e.g. 'API kya hota hai? Chaliye ek simple example se samajhte hain...'). Strictly DO NOT use Devanagari script."
    else:
        return chunk_text

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            f"""You are a professional audio transcript translator and transliterator.
Convert the dialogue of each line in the transcript according to this instruction:
{inst}

CRITICAL RULES:
1. Every line format MUST remain strictly: `[MM:SS] Speaker X: <dialogue>`
2. NEVER remove, change, or omit the timestamp `[MM:SS]` or speaker label `Speaker X:`.
3. Output ONLY the converted lines. Do NOT write any greetings, preamble, markdown formatting, or explanation."""
        ),
        ("human", "{transcript}")
    ])

    try:
        chain = prompt | get_llm(model="openai/gpt-oss-120b", max_tokens=1500) | StrOutputParser()
        result = chain.invoke({"transcript": chunk_text}).strip()
        return result
    except Exception as e:
        print(f"Error converting transcript chunk: {e}")
        return chunk_text


def convert_transcript_format(transcript: str, target_format: str) -> str:
    """Converts transcript dialogue into translated English, Hindi font (Devanagari), or Roman Hinglish."""
    if not transcript or not transcript.strip():
        return transcript

    fmt = (target_format or "translated").lower().strip()
    if fmt not in ["translated", "hindi", "hindi_font", "devanagari", "romanized", "roman_transliteration", "hinglish"]:
        return transcript

    lines = transcript.splitlines()
    if not lines:
        return transcript

    # Process in batches of 25 lines to avoid token and output length limits
    batch_size = 25
    converted_parts = []

    for i in range(0, len(lines), batch_size):
        batch = lines[i:i + batch_size]
        batch_text = "\n".join(batch)
        converted_chunk = convert_transcript_chunk(batch_text, target_format)
        converted_parts.append(converted_chunk)

    return "\n".join(converted_parts).strip()


def convert_meeting_insights(insights: dict, target_format: str) -> dict:
    """Converts an existing meeting's title, summary, action_items, key_decisions, and questions to target format."""
    fmt = (target_format or "translated").lower().strip()
    lang_inst = get_format_instruction(fmt)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            f"""You are an expert meeting translator.
Translate the following meeting insights into the target format:
{lang_inst}

Return ONLY valid JSON in this exact structure:
{{{{
  "title": "short translated title",
  "summary": "detailed markdown summary in target format",
  "action_items": ["item1", "item2"],
  "key_decisions": ["decision1", "decision2"],
  "open_questions": ["question1", "question2"]
}}}}

Return ONLY valid JSON. Do not wrap in ```json or add any other text outside the JSON."""
        ),
        (
            "human",
            """Title: {title}
Summary: {summary}
Action Items: {action_items}
Key Decisions: {key_decisions}
Open Questions: {open_questions}"""
        )
    ])

    parser = JsonOutputParser()
    try:
        llm = get_llm_json(model="openai/gpt-oss-120b")
        chain = prompt | llm | parser
        converted = chain.invoke({
            "title": insights.get("title", "Meeting"),
            "summary": insights.get("summary", ""),
            "action_items": str(insights.get("action_items", [])),
            "key_decisions": str(insights.get("key_decisions", [])),
            "open_questions": str(insights.get("open_questions", [])),
        })

        return {
            "title": converted.get("title") or insights.get("title", "Meeting"),
            "summary": (converted.get("summary") or insights.get("summary", "")).replace("\\n", "\n"),
            "action_items": converted.get("action_items") if isinstance(converted.get("action_items"), list) else [],
            "key_decisions": converted.get("key_decisions") if isinstance(converted.get("key_decisions"), list) else [],
            "open_questions": converted.get("open_questions") if isinstance(converted.get("open_questions"), list) else [],
        }
    except Exception as e:
        print(f"Error converting meeting insights: {e}")
        return insights


def extract_meeting_insights(transcript: str, output_format: str = "translated") -> dict:
    if not transcript or not transcript.strip():
        return {
            "title": "Untitled Meeting",
            "summary": "No speech detected in this recording.",
            "action_items": [],
            "key_decisions": [],
            "open_questions": []
        }

    lang_inst = get_format_instruction(output_format)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            f"""You are an expert meeting and video analyst.

Analyze the transcript and return ONLY valid JSON in the following format:

{{{{
  "title": "short meeting title",
  "summary": "detailed markdown summary",
  "action_items": ["item1", "item2"],
  "key_decisions": ["decision1", "decision2"],
  "open_questions": ["question1", "question2"]
}}}}

Instructions:
1. "title": A concise, descriptive title (maximum 8 words).
2. "summary": A comprehensive, high-value summary of the transcript formatted in markdown. Include an Executive Summary, Detailed Discussion by topic, and Key Takeaways.
3. "action_items": A list of concrete tasks, commands, or next steps mentioned. Return [] if none.
4. "key_decisions": A list of important conclusions, decisions, or core takeaways. Return [] if none.
5. "open_questions": A list of unresolved questions, concerns, or blockers. Return [] if none.
{lang_inst}

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