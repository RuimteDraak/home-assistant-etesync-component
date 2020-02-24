import voluptuous as vol
import logging
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


CONF_ENCRYPTION_PASSWORD = "encryption_password"

DOMAIN = "etesync_calendar"

CALENDAR_ITEM_TYPE = "CALENDAR"

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

    # Token should be saved instead of requested every time
    auth_token = Authenticator(url).get_auth_token(username, password)

    etesync = EteSync(username, auth_token, remote=url)
    _LOGGER.info("Deriving key")
    # Very slow operation, should probably be securely cached
    etesync.derive_key(encryption_password)
    _LOGGER.info("Syncing")
    etesync.sync()
    _LOGGER.info("Syncing done")

    items = etesync.list()

    devices = []

    for item in items:
        # Filter task list / address book's
        if item.info['type'] == CALENDAR_ITEM_TYPE:
            entity_id = generate_entity_id(ENTITY_ID_FORMAT, item.info['displayName'].lower(), hass=hass)
            device = EteSyncCalendarEventDevice(item, entity_id)
            devices.append(device)

    add_entities(devices, True)


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
