import random

import spot

import re


def regex_spin_to_spot(expression: str) -> str:
    # --- Step 1: Strip 'ltl <label> {' and trailing '}' ---
    expression = expression.strip()
    expression = re.sub(r"^ltl\s+\w+\s*{", "", expression).strip()
    expression = re.sub(r"}\s*$", "", expression).strip()

    # --- Step 2: Wrap in <> if not already ---
    if not expression.startswith("<>"):
        expression = f"<>({expression})"

    return expression


def add_init_state(expression: str) -> str:
    # --- Step 1: Strip 'ltl <label> {' and trailing '}' ---
    expression = expression.strip()
    expression = re.sub(r"^ltl\s+\w+\s*{", "", expression).strip()
    expression = re.sub(r"}\s*$", "", expression).strip()

    expression = f"init && X ({expression})"

    return "ltl mission { " + expression + " }"


def init_state_macro(macros: str) -> str:
    # ensure that the initial state is defined in the LTL macros
    updated_macros: str = ""
    if "#define init" not in macros:
        match = re.search(r"\(.*?==", macros)
        if match is not None:
            first_line = match.group(0)
            init_macro = "#define init " + first_line + " 0)\n"
            updated_macros = init_macro + macros
    return updated_macros


def generate_accepting_run_string(aut) -> str:
    curr = aut.get_init_state_number()
    path = []
    while not aut.state_is_accepting(curr):
        edges = [e for e in aut.out(curr)]

        next = curr
        while next == curr:
            sel_e = random.choice(edges)
            next = sel_e.dst

        # move
        curr = next

        path.append(spot.bdd_format_formula(aut.get_dict(), sel_e.cond))

    return " ".join(path)


def count_ltl_tasks(aut) -> int:
    # count transitions without self-loops
    return sum(1 for s in range(aut.num_states()) for t in aut.out(s) if t.dst != s)
