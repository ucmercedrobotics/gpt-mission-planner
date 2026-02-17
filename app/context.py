from pathlib import Path
from jinja2 import Environment, FileSystemLoader


def load_template(template_name: str, variables: dict = None) -> str:
    if variables is None:
        variables = {}

    current_dir = Path(__file__).parent
    prompts_dir = current_dir / "system_prompts"

    env = Environment(
        loader=FileSystemLoader(prompts_dir),
        trim_blocks=True,
        # lstrip_blocks=True,
    )

    template = env.get_template(f"{template_name}.j2")

    return template.render(**variables)
