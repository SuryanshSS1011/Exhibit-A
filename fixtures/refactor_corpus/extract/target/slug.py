def normalize_label(value: str) -> str:
    return _join_words(_words(value))


def _words(value: str) -> list[str]:
    return value.strip().lower().split()


def _join_words(words: list[str]) -> str:
    return "-".join(words)
