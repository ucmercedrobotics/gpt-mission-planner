import logging

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat.chat_completion import ChatCompletion


class GPTInterface:
    def __init__(
        self,
        logger: logging.Logger,
        token_path: str,
        max_tokens: int = 2000,
        temperature: float = 0.2,
    ):
        self.logger: logging.Logger = logger
        # max number of tokens that GPT will respond with, almost 1:1 with words to token
        self.max_tokens: int = max_tokens
        # creativity of ChatGPT
        self.temperature: float = temperature
        # loading in secret API token from your env file
        load_dotenv(token_path)
        # binding GPT client
        self.client: OpenAI = OpenAI()

    def init_context(self, schema_path: str, context_files: list[str]):
        # all robots must come with a schema
        self._set_schema(schema_path)

        # default context
        self.context: list = [
            {
                "role": "system",
                "content": "You are a mission planner that generates navigational XML mission plans based on robotic task representation. \
                            When asked to generate a mission, create an XML file conformant to the known schema and \
                            use the context files to provide references in the mission plan for how the robot tasked with this mission should go about doing it. \
                            Within the context files you'll find information that should enable you to determine how the mission should operate. \
                            If not, simply state that the mission is unachieveable and requires more information. \
                            Place the original question in the TaskDescription element of the CompositeTaskInformation element for logging.",
            },
            # context
            {
                "role": "user",
                "content": "This is the schema for which you must generate mission plan XML documents. \
                            The mission must be syntactically correct and validate using an XML linter: "
                + self.schema,
            },
        ]

        # this could be empty
        if len(context_files) > 0:
            self.context += self._add_additional_context_files(context_files)

        self.initial_context_length = len(self.context)

    def add_context(self, user: str, assistant: str | None) -> None:
        # generate new GPT API dict string context
        new_user_context = {"role": "user", "content": user}
        new_assistant_context = {"role": "assistant", "content": assistant}
        # append to pre-existing context
        self.context.append(new_user_context)
        self.context.append(new_assistant_context)

    def reset_context(self):
        self.context = self.context[0 : self.initial_context_length]

    # TODO: should we expose OpenAI object or string response?
    def ask_gpt(self, prompt: str, add_context: bool = False) -> str | None:
        message: list = self.context.copy()
        message.append({"role": "user", "content": prompt})

        completion: ChatCompletion = self.client.chat.completions.create(
            model="gpt-4o",
            messages=message,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

        response: str | None = completion.choices[0].message.content

        if add_context:
            self.add_context(prompt, response)

        return response

    def _set_schema(self, schema_path: str) -> None:
        # Read XSD from 1872.1-2024
        with open(schema_path, "r") as file:
            self.schema = file.read()

    def _add_additional_context_files(self, context_files: list[str]) -> list[dict]:
        context_list: list[dict] = []
        for c in context_files:
            with open(c, "r") as file:
                extra = file.read()
            context_list.append(
                {
                    "role": "user",
                    "content": "Use this additional file to provide context when generating XML mission plans. \
                            The content within should be self explanatory: "
                    + extra,
                }
            )

        return context_list
