import os
import logging

from datetime import timedelta
from typing import List, Tuple, Optional

_LOGGER = logging.getLogger(__name__)

CACHE_FILE_TEXT = 'secret_check'
CACHE_FILE_BIN = 'secret_key'


def parse(entries: List[Tuple[str, str]]) -> dict:
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

        if key.startswith(('dtstart', 'dtend')):
            key, value = _parse_keyed_timezone(key, value)

        if key == 'rrule':
            value = _parse_repeating(value)

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


def _parse_repeating(value: str):
    values = value.split(';')

    result = {}
    for v in values:
        split = v.split('=', 1)
        result[split[0].lower()] = split[1].lower()
    return result


def _parse_keyed_timezone(key: str, value: str):
    if ';' not in key or '=' not in key:
        return key, value

    # DTSTART;TZID=Europe/Amsterdam:20200612T170000
    # DTSTART;VALUE=DATE:20200420

    splitted = key.split(';', 1)
    timezone = splitted[-1].split('=')[-1]
    return (splitted[0], {
        'timezone': timezone,
        'time': value
    })


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


def parse_iso8601_duration(duration_text: str) -> Optional[timedelta]:
    """
            Parse an ISO 8601 duration into a timedelta
            https://en.wikipedia.org/wiki/ISO_8601#Durations
            example param: P3Y6M4DT12H30M5S
                           PT3600S

            :param duration_text: Duration as string in ISO 8601 format
            :return: datetime.timedelta based on duration_text param
            """
    if duration_text is None:
        return None

    qualifiers = 'YWDHMS'

    years, months, weeks, days, hours, minutes, seconds = 0, 0, 0, 0, 0, 0, 0

    period = False
    number = ''

    for char in duration_text:
        if char == 'P':
            period = True
            continue
        if char == 'T':
            period = False
            continue

        if char in qualifiers:
            # handle number with current qualifier

            if char == 'Y':  # years
                years = int(number)
            elif char == 'W':  # weeks
                weeks = int(number)
            elif char == 'D':  # days
                days = int(number)
            elif char == 'H':  # hours
                hours = int(number)
            elif char == 'S':  # seconds
                seconds = int(number)
            elif char == 'M':  # months or minutes
                if period:  # months
                    months = int(number)
                    _LOGGER.warning("months in duration %s not supported, ignored", duration_text)
                else:  # minutes
                    minutes = int(number)

            number = ''
            continue

        if char.isnumeric():
            number += char
            pass
        else:
            pass

    total_days = years * 365 + weeks * 7 + days
    total_seconds = hours * 60 * 60 + minutes * 60 + seconds
    return timedelta(days=total_days, seconds=total_seconds)
