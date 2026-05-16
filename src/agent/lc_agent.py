import logging
import uuid
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage
from src.llm.lc_provider import build_chat_model
from src.agent.lc_tools import build_lc_tools
from src.memory.lc_history import SQLiteChatMessageHistory
import src.config as cfg

logger = logging.getLogger(__name__)
_make_tools = build_lc_tools


def _default_system_prompt() -> str:
    from src.agent.agent import SYSTEM_PROMPT
    return SYSTEM_PROMPT


class LangChainAgent:
    last_usage = None

    def __init__(
        self,
        system_prompt: str = "",
        verbose: bool = True,
        session_id=None,
        chat_history=None,
        prompt_name: str = "",
        skills=None,
        thinking_config=None,
        user_memory=None,
        **kwargs,
    ):
        self._session_id = session_id or str(uuid.uuid4())
        self._system_prompt = system_prompt or _default_system_prompt()
        self.verbose = verbose
        self._prompt_name = prompt_name
        self.thinking_cfg = thinking_config
        self._skill_bodies = {}
        if skills:
            self._skill_bodies = {n: info.body for n, info in skills.items()}
        self._history = SQLiteChatMessageHistory(self._session_id)
        self._llm = build_chat_model()
        self._tl = _make_tools(self._skill_bodies if self._skill_bodies else None)
        self._agent = create_agent(self._llm, self._tl, **{"system_prompt": self._system_prompt})

    @property
    def session_id(self):
        return self._session_id

    def run(self, user_input: str) -> str:
        msgs = self._history.messages + [HumanMessage(content=user_input)]
        try:
            result = self._agent.invoke({"messages": msgs})
            ai_msgs = [m for m in result["messages"] if isinstance(m, AIMessage)]
            answer = ai_msgs[-1].content.strip() if ai_msgs else ""
        except Exception as e:
            logger.error("LangChainAgent.run error: %s", e)
            answer = "Error: " + str(e)
        self._history.add_message(HumanMessage(content=user_input))
        self._history.add_message(AIMessage(content=answer))
        return answer

    def chat(self, user_input: str) -> str:
        return self.run(user_input)

    def activate_skill(self, name: str, body: str) -> bool:
        open_tag = f'<skill_content name="{name}">'
        if open_tag in self._system_prompt:
            return False
        self._system_prompt += f"\n\n{open_tag}\n{body}\n</skill_content>"
        self._skill_bodies.pop(name, None)
        self._tl = _make_tools(self._skill_bodies if self._skill_bodies else None)
        self._agent = create_agent(self._llm, self._tl, **{"system_prompt": self._system_prompt})
        logger.info("[LangChainAgent] Skill [%s] activated", name)
        return True
