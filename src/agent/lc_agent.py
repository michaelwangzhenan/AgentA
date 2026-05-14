import logging
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage
from src.llm.lc_provider import build_chat_model
from src.agent.lc_tools import build_lc_tools
from src.memory.lc_history import SQLiteChatMessageHistory
import src.config as cfg

logger = logging.getLogger(__name__)
_SYSTEM_PROMPT = 'You are a helpful AI assistant. Use the search_knowledge tool first for domain questions, then fetch_url for real-time info.'

class LangChainAgent:
    def __init__(self, session_id: str, skill_bodies: dict = None,
                 temperature: float = 0.7, db_path: str = None):
        self._session_id = session_id
        self._skill_bodies = skill_bodies or {}
        self._history = SQLiteChatMessageHistory(session_id, db_path)
        self._llm = build_chat_model(temperature)
        self._tools = build_lc_tools(skill_bodies if skill_bodies else None)
        self._agent = create_agent(self._llm, self._tools, system_prompt=_SYSTEM_PROMPT)

    def chat(self, user_input: str) -> str:
        msgs = self._history.messages + [HumanMessage(content=user_input)]
        try:
            result = self._agent.invoke({'messages': msgs})
            ai_msgs = [m for m in result['messages'] if isinstance(m, AIMessage)]
            answer = ai_msgs[-1].content if ai_msgs else ''
        except Exception as e:
            logger.error('LangChainAgent error: %s', e)
            answer = 'Error: ' + str(e)
        self._history.add_message(HumanMessage(content=user_input))
        self._history.add_message(AIMessage(content=answer))
        return answer

    def _format_history(self) -> str:
        parts = []
        for m in self._history.messages:
            if isinstance(m, HumanMessage):
                parts.append('User: ' + m.content)
            elif isinstance(m, AIMessage):
                parts.append('Assistant: ' + m.content)
        return chr(10).join(parts)
