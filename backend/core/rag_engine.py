import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from core.vector_store import build_vector_store, load_vector_store, get_retriever

load_dotenv()

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
DEFAULT_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "1024"))


def _resolve_model_and_tokens(model: str = None, max_tokens: int = None):
    target_model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)
    limit = max_tokens or DEFAULT_MAX_TOKENS
    # On Groq on-demand tier, qwen/qwen3.8-27b has a strict 1000 OTPM limit.
    # Cap requested tokens at 800 to avoid 429 rate limit errors.
    if "qwen" in target_model.lower() and limit > 800:
        limit = 800
    return target_model, limit


def get_llm(model: str = None, max_tokens: int = None):
    target_model, limit = _resolve_model_and_tokens(model, max_tokens)
    return ChatGroq(
        model=target_model,
        groq_api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.3,
        max_tokens=limit,
    )


def get_llm_json(model: str = None, max_tokens: int = None):
    """JSON-mode LLM — used wherever structured JSON output is required."""
    target_model, limit = _resolve_model_and_tokens(model, max_tokens)
    return ChatGroq(
        model=target_model,
        groq_api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.2,
        max_tokens=limit,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def format_docs(docs):
    return "\n\n".join([doc.page_content for doc in docs])


def build_rag_chain(transcript: str, meeting_id: str, model: str = None):
    vector_store = build_vector_store(transcript, meeting_id)
    retriever = get_retriever(vector_store, k=4)
    llm = get_llm(model=model)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are an expert meeting assistant. Answer the user's question 
based ONLY on the meeting transcript context provided below.

If the answer is not found in the context, say: 
"I could not find this information in the meeting transcript."

Always be concise and precise. If quoting someone, mention it clearly.

Context from meeting transcript:
{context}""",
        ),
        ("human", "{question}"),
    ])

    rag_chain = (
        {
            "context": retriever | RunnableLambda(format_docs),
            "question": RunnablePassthrough(),
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    return rag_chain


def load_rag_chain(meeting_id: str, model: str = None):
    vector_store = load_vector_store(meeting_id)
    retriever = get_retriever(vector_store)
    llm = get_llm(model=model)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are an expert meeting assistant. Answer the user's question 
based ONLY on the meeting transcript context provided below.

If the answer is not found in the context, say: 
"I could not find this information in the meeting transcript."

Always be concise and precise. If quoting someone, mention it clearly.

Context from meeting transcript:
{context}""",
        ),
        ("human", "{question}"),
    ])

    rag_chain = (
        {
            "context": retriever | RunnableLambda(format_docs),
            "question": RunnablePassthrough(),
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    return rag_chain


def ask_questions(rag_chain, question: str) -> str:
    answer = rag_chain.invoke(question)
    return answer