import re


_BOUNDARY = re.compile(r"([.!?…]+[\"'”’»)]*)(?:[ \t]+|[\r\n]+)|[\r\n]+")


def split_sentences(text: str) -> list[str]:
    """Tách theo dấu câu hoặc xuống dòng, không thay đổi nội dung câu."""
    text = text.strip()
    if not text:
        return []
    rough: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        punctuation = match.group(1)
        end = match.start() + len(punctuation) if punctuation else match.start()
        part = text[start:end].strip()
        if part:
            rough.append(part)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        rough.append(tail)
    return rough
