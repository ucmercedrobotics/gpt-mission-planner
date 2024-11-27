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

    def init_context(self, schema_path: str, farm_layout_path: str):
        self._set_schema(schema_path)
        self._set_farm_layout(farm_layout_path)

        # default context
        self.context: list = [
            {
                "role": "system",
                "content": "You are a mission planner that generates navigational XML mission plans based on robotic task representation. \
                            When asked to generate a mission, create an XML file conformant to the known schema and \
                            use the GeoJSON file to provide references in the mission plan for things such as GPS location, tree type, etc. \
                            In order to accomplish most actions, you must first drive to the location. \
                            Therefore, tasks should almost always require driving to a tree and then doing an action unless doing multiple actions at the same tree. \
                            Place the original question in the TaskDescription element of the CompositeTaskInformation element for logging. \
                            If possible, comment the total estimated haversine distance traveled from start to end somewhere in the Outcome, but don't show how you computed it.",
            },
            # context
            {
                "role": "user",
                "content": "This is the schema for which you must generate mission plan XML documents. \
                            The mission must be syntactically correct and validate using an XML linter: "
                + self.schema,
            },
            {
                "role": "assistant",
                "content": "If you have any specific questions or modifications you'd like to discuss regarding this schema, feel free to ask!",
            },
            {
                "role": "user",
                "content": "This is the GeoJSON for which you must generate mission plan XML documents. This is our orchard: "
                + self.farm_layout,
            },
            {
                "role": "assistant",
                "content": "Thank you for providing the GeoJSON file. \
                            I'll assist you in creating the XML file for your robotic mission plan when you provide your mission.",
            },
            # TODO: add context of farm layout so that machine can generate XML with relevant state information
        ]

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

    def _set_farm_layout(self, farm_layout_path: str) -> None:
        # Read XSD from 1872.1-2024
        with open(farm_layout_path, "r") as file:
            self.farm_layout = file.read()
