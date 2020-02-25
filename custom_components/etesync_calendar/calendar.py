import voluptuous as vol
import logging
import os
from etesync import Authenticator, EteSync

from homeassistant.components.calendar import (
    ENTITY_ID_FORMAT,
    PLATFORM_SCHEMA,
    CalendarEventDevice
)


from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import generate_entity_id

DOMAIN = 'etesync_calendar'

CONF_ENCRYPTION_PASSWORD = 'encryption_password'
CACHE_FOLDER = 'custom_components/etesync_calendar/cache'
CACHE_FILE_TEXT = 'secret_check'
CACHE_FILE_BIN = 'secret_key'

CALENDAR_ITEM_TYPE = 'CALENDAR'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_URL): vol.Url(),
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_ENCRYPTION_PASSWORD): cv.string,

        # vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
    }
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, disc_info=None):
    url = config[CONF_URL]
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]
    encryption_password = config[CONF_ENCRYPTION_PASSWORD]

    cache_folder = hass.config.path(CACHE_FOLDER)
    credentials = _read_from_cache(cache_folder)

    if credentials and _credentials_not_changed((url, username, password), credentials):
        url, username, password, auth_token, cipher_key = credentials
        ete_sync = EteSync(username, auth_token, remote=url, cipher_key=cipher_key)
    else:
        # Token should be saved instead of requested every time
        auth_token = Authenticator(url).get_auth_token(username, password)

        ete_sync = EteSync(username, auth_token, remote=url)
        _LOGGER.warning("Deriving key, this could take some time")
        # Very slow operation, should probably be securely cached
        cipher_key = ete_sync.derive_key(encryption_password)
        _write_to_cache(cache_folder, url, username, password, encryption_password, cipher_key)

    _LOGGER.warning("Syncing")
    ete_sync.sync()
    _LOGGER.warning("Syncing done")

    items = ete_sync.list()

    devices = []

    for item in items:
        # Filter task list / address book's
        if item.info['type'] == CALENDAR_ITEM_TYPE:
            entity_id = generate_entity_id(ENTITY_ID_FORMAT, item.info['displayName'].lower(), hass=hass)
            device = EteSyncCalendarEventDevice(item, entity_id)
            devices.append(device)

    add_entities(devices, True)


def _read_from_cache(folder):
    file_t = os.path.join(folder, CACHE_FILE_TEXT)
    file_w = os.path.join(folder, CACHE_FILE_BIN)
    if os.path.exists(file_t) and os.path.isfile(file_t):
        try:
            with open(file_t, 'tr') as stream:
                url = stream.readline()
                username = stream.readline()
                password = stream.readline()
                auth_token = stream.readline()
            with open(file_w, 'br') as stream:
                cipher_key = stream.read()
            return url, username, password, auth_token, cipher_key
        except IOError:
            os.remove(file_t)
    return None


def _write_to_cache(folder, url, username, password, encryption_password, cipher_key):
    if not os.path.exists(folder):
        os.makedirs(folder)

    file_t = os.path.join(folder, CACHE_FILE_TEXT)
    file_b = os.path.join(folder, CACHE_FILE_BIN)
    try:
        with open(file_t, 'tw') as stream:
            stream.write('\n'.join([url, username, password, encryption_password]))
        with open(file_b, 'bw') as stream:
            stream.write(cipher_key)
    except IOError:
        _LOGGER.warning("Could not write cache file")


def _credentials_not_changed(old, new):
    for i in range(3):
        if not old[i] == new[i]:
            _LOGGER.warning("credentials have changed!")
            return False
    return True


class EteSyncCalendarEventDevice(CalendarEventDevice):
    """A device for a single etesync calendar."""

    def __init__(self, calendar, entity_id):
        self._calendar = calendar
        self._name = calendar.info['displayName']
        self._entity_id = entity_id

    @property
    def event(self):
        return None

    async def async_get_events(self, hass, start_date, end_date):
        pass
