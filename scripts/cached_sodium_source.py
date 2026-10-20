"""Use the already verified upstream source input, avoiding build-time fetches."""
from pathlib import Path


def arguments(dependencies, root):
    if not any(p['directory'] == 'libsodium-source' for p in dependencies):
        return []  # Retained immutable inputs from before source caching.
    source = Path(root) / 'libsodium-source/libsodium-1.0.22'
    if not (source / 'configure').is_file() or not (source / 'src/libsodium/include/sodium/version.h.in').is_file():
        raise ValueError('The verified libsodium source cache is incomplete')
    return ['-DFETCHCONTENT_SOURCE_DIR_FATBERRY_SODIUM_SRC=' + str(source)]
