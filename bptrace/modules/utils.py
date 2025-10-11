"""Utility functions for bptrace."""

from typing import Generator

def chunk_list(lst: list, n: int = 200) -> Generator[list, None, None]:
    """Split a list into fixed-size chunks."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]
