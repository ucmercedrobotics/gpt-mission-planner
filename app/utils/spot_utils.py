import random

import spot

import re


def regex_spin_to_spot(expression: str) -> str:
    # --- Step 1: Strip 'ltl <label> {' and trailing '}' ---
    expression = expression.strip()
    expression = re.sub(r"^ltl\s+\w+\s*{", "", expression).strip()
    expression = re.sub(r"}\s*$", "", expression).strip()

    # --- Step 2: Deduplicate (A == 0 && X(A == ...) ---
    def dedup_initial_action_zero(match):
        name1, name2, inner = match.group(1), match.group(2), match.group(3)
        if name1 == name2:
            # Return just name1 lowercase plus the rest inside inner (which is the rest of the expression inside the parentheses)
            return f"{name1.lower()} && {inner}"
        return match.group(0)

    expression = re.sub(
        r"\(\s*([A-Za-z0-9_]+)\.action\.actionType\s*==\s*0\s*&&\s*X\s*\(\s*([A-Za-z0-9_]+)\.action\.actionType\s*==\s*[A-Za-z0-9_]+\s*&&\s*(.+)\)\s*\)",  # notice the outer closing parens at end
        dedup_initial_action_zero,
        expression,
        flags=re.DOTALL,
    )

    expression = re.sub(
        r"\(\s*([A-Za-z0-9_]+)\.action\.actionType\s*==\s*0\s*&&\s*X\s*\(\s*([A-Za-z0-9_]+)\.action\.actionType\s*==\s*[A-Za-z0-9_]+\s*&&",
        dedup_initial_action_zero,
        expression,
    )

    # --- Step 3: Remove outer parentheses if any ---
    expression = expression.strip()
    if expression.startswith("(") and expression.endswith(")"):
        expression = expression[1:-1].strip()

    # --- Step 4: Replace .action.actionType == something ---
    expression = re.sub(
        r"\b([A-Za-z0-9_]+)\.action\.actionType\s*==\s*[A-Za-z0-9_]+",
        lambda m: m.group(1).lower(),
        expression,
    )

    # --- Step 5: Replace comparisons with labels and negations ---
    def replace_comparator(match):
        var = match.group(1).lower()
        op = match.group(2)
        val = match.group(3)

        mapping = {
            "<": ("low", False),
            ">=": ("low", True),
            "<=": ("high", False),
            ">": ("high", True),
            "==": ("equal", False),
            "!=": ("equal", True),
        }

        if op not in mapping:
            return match.group(0)

        prefix, neg = mapping[op]
        token = f"{prefix}{var}_{val}"

        if neg:
            return f"!{token}"
        else:
            return token

    expression = re.sub(
        r"\b([A-Za-z0-9_]+)\s*(<=|>=|<|>|==|!=)\s*(-?\d+(?:\.\d+)?)",
        replace_comparator,
        expression,
    )

    # --- Step 6: Clean logical operators spacing ---
    expression = re.sub(r"\s*(&&|\|\|)\s*", r" \1 ", expression)

    # --- Step 7: Ensure space before temporal operator X( ---
    expression = re.sub(r"\s*X\s*\(", r" X(", expression)

    # --- Step 8: Remove line breaks and collapse whitespace ---
    expression = re.sub(r"\s+", " ", expression).strip()

    # --- Step 10: Wrap in <> if not already ---
    if not expression.startswith("<>"):
        expression = f"<>({expression})"

    return expression


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
