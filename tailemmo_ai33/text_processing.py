PUNCTUATION = (".", ",", ";", ":", "?", "!")
LONG_PUNCTUATION = frozenset(".?!")


def add_punctuation_pauses(text, selected, short_seconds=0.35, long_seconds=0.70):
    """Insert AI33 break tags after selected punctuation without touching existing tags."""
    source = str(text or "")
    selected = set(selected or ()) & set(PUNCTUATION)
    if not source or not selected:
        return source

    output = []
    index = 0
    square_depth = 0
    angle_depth = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char == "[":
            square_depth += 1
        elif char == "]" and square_depth:
            square_depth -= 1
        elif char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth:
            angle_depth -= 1

        if square_depth or angle_depth or char not in PUNCTUATION:
            output.append(char)
            index += 1
            continue

        end = index + 1
        while end < length and source[end] in PUNCTUATION:
            end += 1
        run = source[index:end]
        output.append(run)

        previous = source[index - 1] if index else ""
        following = source[end] if end < length else ""
        numeric_separator = (
            len(run) == 1 and char in ".,:" and previous.isdigit() and following.isdigit()
        )
        url_colon = char == ":" and source[max(0, index - 5):index].lower().endswith(("http", "https"))
        token_start = index
        while token_start > 0 and not source[token_start - 1].isspace():
            token_start -= 1
        inside_url = "://" in source[token_start:index] and bool(following and not following.isspace())
        should_pause = (
            any(mark in selected for mark in run)
            and not numeric_separator
            and not url_colon
            and not inside_url
        )
        lookahead = end
        while lookahead < length and source[lookahead].isspace():
            lookahead += 1
        already_has_break = source[lookahead:lookahead + 6].lower() == "<break"
        if should_pause and not already_has_break:
            seconds = long_seconds if any(mark in LONG_PUNCTUATION for mark in run) else short_seconds
            output.append(f' <break time="{float(seconds):g}s" />')
        index = end
    return "".join(output)
