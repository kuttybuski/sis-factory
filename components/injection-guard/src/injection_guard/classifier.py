from typing import Protocol


class Classifier(Protocol):
    """Optional ML model plugged into scan(). Returns the probability (0..1) that the text is an injection.

    Not bundled. A permissively licensed open-source model (for example a DeBERTa prompt-injection classifier)
    can be wrapped behind this interface; check its license and benchmarks before relying on it.
    """

    def score(self, text: str) -> float: ...
