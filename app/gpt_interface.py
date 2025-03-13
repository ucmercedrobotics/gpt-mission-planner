import logging

from dotenv import load_dotenv
import aisuite as ai

from context import iros_2025_context, verification_agent_context


class LLMInterface:
    def __init__(
        self,
        logger: logging.Logger,
        token_path: str,
        model: str = "openai:gpt-4o",
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
        # binding LLM client
        self.client: ai.Client = ai.Client()
        # which model to use?
        self.model: str = model
        # schema text
        self.schemas: str = ""
        # context
        self.context: list = []
        # input template file provided when wanting spin verification
        self.promela_template: str = ""

    def init_context(self, schema_path: list[str], context_files: list[str]):
        for s in schema_path:
            # all robots must come with a schema
            self._set_schema(s)

        # context can be updated from context.py
        self.context = iros_2025_context(self.schemas)

        # this could be empty
        if context_files is not None:
            if len(context_files) > 0:
                self.context += self._add_additional_context_files(context_files)

        self.initial_context_length = len(self.context)

    def init_promela_context(
        self,
        schema_path: list[str],
        promela_template: str,
        context_files: list[str],
    ):
        # TODO: I think we need a list of task names or a way to format the task naming based on some kind of standard
        for s in schema_path:
            # all robots must come with a schema
            self._set_schema(s)

        # default context
        self.context = verification_agent_context(promela_template)

        # this could be empty
        if context_files is not None:
            if len(context_files) > 0:
                self.context += self._add_additional_context_files(context_files)

        self.initial_context_length = len(self.context)

    def add_context(self, user: str, assistant: str | None = None) -> None:
        # generate new GPT API dict string context
        new_user_context = {"role": "user", "content": user}
        # append to pre-existing context
        self.context.append(new_user_context)
        # do the same if you want to capture response
        if assistant is not None:
            new_assistant_context = {"role": "assistant", "content": assistant}
            self.context.append(new_assistant_context)

    def reset_context(self, context_count: int):
        self.context = self.context[0:context_count]

    # TODO: should we expose OpenAI object or string response?
    def ask_gpt(self, prompt: str, add_context: bool = False) -> str | None:
        message: list = self.context.copy()
        message.append({"role": "user", "content": prompt})

        completion = self.client.chat.completions.create(
            model=self.model, messages=message, temperature=self.temperature
        )

        response: str | None = completion.choices[0].message.content

        if add_context:
            self.add_context(prompt, response)

        return response

    def _set_schema(self, schema_path: str) -> None:
        # Read XSD from 1872.1-2024
        with open(schema_path, "r") as file:
            self.schemas += file.read()

        self.schemas += "\nThis schema is located at path: " + schema_path

        # deliniate for chatgpt
        self.schemas += "\nnext schema: "

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
