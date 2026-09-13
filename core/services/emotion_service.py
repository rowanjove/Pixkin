"""Emotion and state tag parsing from model outputs."""

import re
from typing import List, Optional, Tuple

from core.pet_animator import PetState

STATE_MAP = {
    "happy": PetState.HAPPY,
    "nod": PetState.NOD,
    "wave": PetState.WAVE,
    "stretch": PetState.STRETCH,
    "alert": PetState.ALERTING,
    "alerting": PetState.ALERTING,
    "thinking": PetState.THINKING,
    "think": PetState.THINKING,
    "sleep": PetState.SLEEP,
    "annoyed": PetState.ANNOYED,
    "success": PetState.SUCCESS,
    "failed": PetState.FAILED,
    "touch": PetState.TOUCH,
    "wake": PetState.WAKE,
}

# Regex to match tags like <state=happy/>, <emotion=nod/>, [state=wave]
TAG_PATTERN = re.compile(r"[<\[](?:state|emotion)\s*=\s*([a-zA-Z_\-]+)\s*(?:/?[>\]])", re.IGNORECASE)


def parse_pet_state(name: str) -> Optional[PetState]:
    """Map state name string to PetState enum."""
    clean = str(name or "").strip().lower()
    if clean in STATE_MAP:
        return STATE_MAP[clean]
    try:
        return PetState(clean)
    except ValueError:
        return None


def extract_emotions(text: str) -> Tuple[str, List[PetState]]:
    """Extract emotion tags from text and return cleaned text along with matched PetStates."""
    if not text:
        return "", []

    emotions: List[PetState] = []

    def _replace(match: re.Match) -> str:
        state_name = match.group(1)
        st = parse_pet_state(state_name)
        if st is not None:
            emotions.append(st)
        return ""

    clean_text = TAG_PATTERN.sub(_replace, text)
    # Strip any redundant extra spaces left behind
    clean_text = re.sub(r" +", " ", clean_text).strip()
    return clean_text, emotions


class StreamingEmotionFilter:
    """Filter emotion tags on the fly during streaming generation."""

    def __init__(self):
        self._partial_tag: str = ""

    def process_chunk(self, chunk: str) -> Tuple[str, List[PetState]]:
        """Process an incoming streaming chunk, buffering potential tags and emitting clean text."""
        combined = self._partial_tag + str(chunk or "")
        self._partial_tag = ""

        emotions: List[PetState] = []

        # Find any complete tags
        def _replace(match: re.Match) -> str:
            st = parse_pet_state(match.group(1))
            if st is not None:
                emotions.append(st)
            return ""

        clean_combined = TAG_PATTERN.sub(_replace, combined)

        # Check if the tail ends with an unclosed tag opening e.g. "<state=" or "<"
        tag_start_idx = max(clean_combined.rfind("<"), clean_combined.rfind("["))
        if tag_start_idx != -1:
            tail = clean_combined[tag_start_idx:]
            # If no closing bracket in tail, it might be an in-progress tag
            if ">" not in tail and "]" not in tail and len(tail) < 30:
                self._partial_tag = tail
                clean_combined = clean_combined[:tag_start_idx]

        return clean_combined, emotions

    def flush(self) -> str:
        """Emit any leftover partial tag text when stream finishes."""
        leftover = self._partial_tag
        self._partial_tag = ""
        return leftover
