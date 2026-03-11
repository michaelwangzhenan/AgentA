# Taget 
Build a Agent that can do the following things:
1. Search for information on the internet
2. Summarize the information found
3. Answer questions based on the summarized information

# Technical Requirements
1. Large language model (LLM) is switchable.
2. Agent can use tools and MCP.
3. Agent can skills, and skills can use tools and MCP.
4. Agent can learn from user interactions and update its knowledge base accordingly.
5. A command start with "/" can be used to trigger specific prompts.

# Steps
1. use a free Chinese LLM (e.g., ChatGLM) to build a prototype of the agent, and then switch to a more powerful LLM for better performance.
2. Integrate web search tools (e.g., Google Search API) to enable the agent to search for information on the internet.
3. Develop a summarization module that can process the information retrieved from the web search and generate concise summaries.
4. Implement a question-answering module that can utilize the summarized information to answer user queries effectively.
5. Create a user interface that allows users to interact with the agent, ask questions, and receive answers in a conversational manner.