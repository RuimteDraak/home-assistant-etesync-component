import os
import logging

_LOGGER = logging.getLogger(__name__)

CACHE_FILE_TEXT = 'secret_check'
CACHE_FILE_BIN = 'secret_key'


def parse(entries: list) -> dict:
    iterator = iter(entries)
    return _parse(iterator)


# Assumes entries is a generator, not a plain list
def _parse(entries) -> dict:
    result = {}
    for entry in entries:
        # Skip malformed (?) entries
        if len(entry) != 2:
            continue
        key, value = entry
        key = key.lower()
        if key == 'begin':
            result[value.lower()] = _parse(entries)
            continue
        elif key == 'end':
            return result

        if result.get(key):
            val = result[key]
            has_append = getattr(val, "append", None)
            if callable(has_append):
                val.append(value)
            else:
                result[key] = [val, value]
        else:
            result[key] = value
    return result


def read_from_cache(folder) -> (str, str, str, []):
    file_t = os.path.join(folder, CACHE_FILE_TEXT)
    file_w = os.path.join(folder, CACHE_FILE_BIN)
    if os.path.exists(file_t) and os.path.isfile(file_t):
        try:
            with open(file_t, 'tr') as stream:
                url = stream.readline().strip()
                username = stream.readline().strip()
                password = stream.readline().strip()
            with open(file_w, 'br') as stream:
                cipher_key = stream.read()
            return url, username, password, cipher_key
        except IOError:
            os.remove(file_t)
    return None


def write_to_cache(folder: str, url: str, username: str, password: str, cipher_key: []):
    if not os.path.exists(folder):
        os.makedirs(folder)

    file_t = os.path.join(folder, CACHE_FILE_TEXT)
    file_b = os.path.join(folder, CACHE_FILE_BIN)
    try:
        with open(file_t, 'tw') as stream:
            stream.write('\n'.join([url, username, password]))
        with open(file_b, 'bw') as stream:
            stream.write(cipher_key)
    except IOError:
        _LOGGER.warning("Could not write cache file")
