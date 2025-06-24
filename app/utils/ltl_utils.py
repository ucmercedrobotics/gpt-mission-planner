from typing import Tuple


def check_parentheses_mismatch(ltl: str) -> Tuple[int, str]:
    unmatched_left: int = 0  # '(' with no matching ')'
    unmatched_right: int = 0  # ')' with no matching '('
    return_match: int = 0  # for balanced parentheses
    return_error: str = ""

    for char in ltl:
        if char == "(":
            unmatched_left += 1
        elif char == ")":
            if unmatched_left > 0:
                unmatched_left -= 1
            else:
                unmatched_right += 1

    if unmatched_left == 0 and unmatched_right == 0:
        return return_match, "Parentheses are balanced"
    elif unmatched_right > 0:
        return_error = f"Unbalanced: Add {unmatched_right} '(' to the left."
    else:
        return_error = f"Unbalanced: Add {unmatched_left} ')' to the right."

    return_match = unmatched_left + unmatched_right

    return return_match, return_error
