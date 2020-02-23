import voluptuous as vol
import logging
from etesync import Authenticator, EteSync

from homeassistant.components.calendar import PLATFORM_SCHEMA, CalendarEventDevice

from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_EMAIL,
    CONF_VERIFY_SSL,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import generate_entity_id


CONF_ENCRYPTION_PASSWORD = "encryption_password"

DOMAIN = "etesync_calendar"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        # pylint: disable=no-value-for-parameter
        vol.Required(CONF_URL): vol.Url(),
        vol.Required(CONF_EMAIL): cv.str,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_ENCRYPTION_PASSWORD): cv.string,

        vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
    }
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, disc_info=None):
    url = config[CONF_URL]
    email = config[CONF_EMAIL]
    password = config[CONF_PASSWORD]
    encryption_password = config[CONF_ENCRYPTION_PASSWORD]

    # Token should be saved intead of requested every time
    auth_token = Authenticator(url).get_auth_token(email, password)

    etesync = EteSync(email, auth_token, remote=url)
    _LOGGER.info("Deriving key")
    # Very slow operation, should probably be securely cached
    etesync.derive_key(encryption_password)
    _LOGGER.info("Syncing")
    etesync.sync()
    _LOGGER.info("Syncing done")

    items = etesync.list()

    _LOGGER.info("Synced {} items", len(items))

    pass
