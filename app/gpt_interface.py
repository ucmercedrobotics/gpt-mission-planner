import logging
import time

from dotenv import load_dotenv
import litellm
from litellm import completion

from context import load_template
from utils.os_utils import read_file

OPENAI_TEMP: float = 1.0
REASONING: str = "low"


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
        # loading in secret API token from your env file
        load_dotenv(token_path)
        # which model to use?
        self.model: str = model
        # context
        self.context: list = []
        self.initial_context_length: int = 0
        # input template file provided when wanting spin verification
        self.promela_template: str = ""
        self.temperature: float = temperature
        self.reasoning: str | None = None
        if "openai" in self.model:
            self.logger.warning(
                f"Using OpenAI model: {self.model}, which requires temperature {OPENAI_TEMP}"
            )
            self.temperature = OPENAI_TEMP
            self.reasoning = REASONING

    def init_context(self, schema_paths: list[str], context_files: list[str]):
        context_files_content = [read_file(f) for f in context_files]
        schemas = [{'path': f, 'content': read_file(f)} for f in schema_paths]

        self.context = [{
            "role": "system",
            "content": load_template("rap_2026", {
                "schemas": schemas,
                "context_files": context_files_content
            })
        }]

        self.initial_context_length = len(self.context)

    def init_promela_context(
        self,
        schema_paths: list[str],
        promela_template: str,
        context_files: list[str],
    ):
        context_files_content = [read_file(f) for f in context_files]
        schemas = [{'path': f, 'content': read_file(f)} for f in schema_paths]

        self.context = [{
            "role": "system",
            "content": load_template("verification_agent", {
                "schema": schemas,
                "promela_template": promela_template,
                "context_files": context_files_content
            })
        }]

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

    def ask_gpt(self, prompt: str, add_context: bool = False) -> str | None:
        answered: bool = False
        message: list = self.context.copy()
        message.append({"role": "user", "content": prompt})

        while not answered:
            try:
                cmp = completion(
                    model=self.model,
                    messages=message,
                    temperature=self.temperature,
                    reasoning_effort=self.reasoning,
                    max_tokens=self.max_tokens,
                )
                answered = True
            except litellm.exceptions.RateLimitError as e:
                self.logger.warning(f"Rate limit error: {e}")
                time.sleep(1)  # wait before retrying

        response: str | None = cmp.choices[0].message.content

        if add_context:
            self.add_context(prompt, response)

        return response
