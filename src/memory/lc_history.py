from typing import List
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.chat_history import BaseChatMessageHistory
from src.memory.chat_history import ChatHistory

class SQLiteChatMessageHistory(BaseChatMessageHistory):
    def __init__(self, session_id: str, db_path: str = None):
        self._session_id = session_id
        self._history = ChatHistory(db_path=db_path) if db_path else ChatHistory()

    @property
    def messages(self) -> List[BaseMessage]:
        raw = self._history.load(self._session_id)
        result = []
        for msg in raw:
            role = msg.get('role', '')
            content = msg.get('content', '')
            if role == 'user':
                result.append(HumanMessage(content=content))
            elif role == 'assistant':
                result.append(AIMessage(content=content))
        return result

    def add_message(self, message: BaseMessage) -> None:
        if isinstance(message, HumanMessage): role = 'user'
        elif isinstance(message, AIMessage): role = 'assistant'
        else: role = 'system'
        self._history.append(self._session_id, {'role': role, 'content': message.content})

    def clear(self) -> None:
        self._history.clear(self._session_id)
