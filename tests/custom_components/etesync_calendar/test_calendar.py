from os import listdir, path, makedirs
import tempfile
import custom_components.etesync_calendar.helpers as helper


def test_write_to_cache():
    folder = path.join(tempfile.gettempdir(), tempfile.gettempprefix(), 'test_write_to_cache')
    url = 'https://test.test'
    username = 'testuser'
    password = 'mypass'
    cipher = bytes([15, 123, 51])

    helper.write_to_cache(folder, url, username, password, cipher)

    assert len(listdir(folder)) == 2


def test_read_from_cache_not_cached_returns_none():
    folder = path.join(tempfile.gettempdir(), tempfile.gettempprefix(), 'test_read_from_cache_not_cached_returns_none')

    assert helper.read_from_cache(folder) is None


def test_read_from_cache_corrupted_returns_none():
    folder = path.join(tempfile.gettempdir(), tempfile.gettempprefix(), 'test_read_from_cache_corrupted_returns_none')

    if not path.exists(folder):
        makedirs(folder)
    # create incomplete file
    with open(path.join(folder, helper.CACHE_FILE_TEXT), 'tw') as file:
        file.write('url\n')
        file.write('user\n')

    assert helper.read_from_cache(folder) is None
    assert not path.exists(path.join(folder, helper.CACHE_FILE_TEXT))


def test_read_from_cache_after_write_returns_result():
    folder = path.join(tempfile.gettempdir(), tempfile.gettempprefix(),
                       'test_read_from_cache_after_write_returns_result')
    url = 'https://test.nl'
    username = 'username'
    password = 'drowssap'
    cipher = bytes([1, 2, 3, 4, 5])

    helper.write_to_cache(folder, url, username, password, cipher)

    result = helper.read_from_cache(folder)

    assert len(result) == 4
    assert result[0] == url
    assert result[1] == username
    assert result[2] == password
    assert result[3] == cipher


def test_parse_empty_returns_empty_dict():
    result = helper.parse([])

    assert result == {}


def test_parse():
    input = [('begin', 'CALENDAR'),
             ('begin', 'EVENT'),
             ('summary', 'do a thing'),
             ('end', 'EVENT'),
             ('end', 'CALENDAR')]

    result = helper.parse(input)

    assert result is not None
    assert result['calendar']['event']['summary'] == 'do a thing'
