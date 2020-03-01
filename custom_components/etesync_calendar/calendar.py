import voluptuous as vol
import logging
import datetime

from etesync import Authenticator, EteSync
from typing import Optional, Dict

from homeassistant.components.calendar import (
    ENTITY_ID_FORMAT,
    PLATFORM_SCHEMA,
    CalendarEventDevice
)


from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    # CONF_VERIFY_SSL,
    STATE_OFF,
    STATE_ON
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.util import Throttle

from .helpers import parse, read_from_cache, write_to_cache

DOMAIN = 'etesync_calendar'

CONF_ENCRYPTION_PASSWORD = 'encryption_password'
CACHE_FOLDER = 'custom_components/etesync_calendar/cache'

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
    credentials = read_from_cache(cache_folder)

    if credentials and _credentials_not_changed((url, username, password), credentials):
        _LOGGER.info("Using cached credentials")
        url, username, password, cipher_key = credentials

        auth_token = Authenticator(url).get_auth_token(username, password)
        ete_sync = EteSync(username, auth_token, remote=url, cipher_key=cipher_key)
    else:
        # Token should be saved instead of requested every time
        auth_token = Authenticator(url).get_auth_token(username, password)

        ete_sync = EteSync(username, auth_token, remote=url)
        _LOGGER.warning("Deriving key, this could take some time")
        # Very slow operation, should probably be securely cached
        cipher_key = ete_sync.derive_key(encryption_password)
        _LOGGER.info("Key derived. Cache result for faster startup times")
        write_to_cache(cache_folder, url, username, password, cipher_key)

    _LOGGER.info("Syncing")
    ete_sync.sync()
    _LOGGER.info("Syncing done")

    items = ete_sync.list()

    devices = []

    for item in items:
        # Filter task list / address book's
        if item.info['type'] == CALENDAR_ITEM_TYPE:
            name = f"{username}-{item.info['displayName']}"
            entity_id = generate_entity_id(ENTITY_ID_FORMAT, name, hass=hass)
            device = EteSyncCalendarEventDevice(item, ete_sync, entity_id)
            devices.append(device)

    add_entities(devices, True)


def _credentials_not_changed(old, new):
    for i in range(3):
        if not old[i] == new[i]:
            _LOGGER.warning("credentials have changed!")
            return False
    return True


class EteSyncCalendarEventDevice(CalendarEventDevice):
    """A device for a single etesync calendar."""

    def __init__(self, calendar, ete_sync, entity_id):
        self._calendar = EteSyncCalendar(calendar, ete_sync)
        self._entity_id = entity_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._calendar.name

    @property
    def event(self):
        return self._calendar.next_event

    @property
    def state_attributes(self):
        event = self.event
        return {
            "message": event.summary,
            "all_day": False,
            "start_time": event.start,
            "end_time": event.end,
            "location": None,
            "description": None,
        }

    @property
    def state(self):
        """Return the state of the calendar event."""
        event = self.event
        if event is None:
            return STATE_OFF

        start = event.start
        end = event.end

        if start is None or end is None:
            return STATE_OFF

        now = datetime.datetime.now()

        if start <= now < end:
            return STATE_ON

        return STATE_OFF

    async def async_get_events(self, hass, start_date, end_date):
        pass

    def update(self):
        self._calendar.update()


class EteSyncCalendar:
    """Class that represents an etesync calendar."""

    def __init__(self, raw_data, ete_sync):
        self._raw_data = raw_data
        self._ete_sync = ete_sync
        self._events = []
        self._build_events()

    def _build_events(self):
        events = self._raw_data.collection.list()
        for event in events:
            self._events.append(EteSyncEvent(event))
        self._events.sort(key=lambda e: e.start)

    @property
    def name(self):
        """Return the name of the Calendar"""
        return self._raw_data.info['displayName']

    @property
    def next_event(self):
        now = datetime.datetime.now()
        for event in self._events:
            if event.end > now:
                return event
        return None

    @Throttle(datetime.timedelta(minutes=5))
    def update(self):
        """Update the calendar data"""
        self._ete_sync.sync()
        # TODO update data
        self._raw_data = self._ete_sync.get(self._raw_data.info['uid'])
        self._build_events()


class EteSyncEvent:
    """Class that represents an etesync event."""

    def __init__(self, event):
        self._raw_event = event
        raw_properties = event.content.splitlines()
        properties = []

        for line in raw_properties:
            key_value = line.split(':', 1)
            properties.append(key_value)

        self._event = parse(properties)

    @property
    def summary(self):
        return self._event['vcalendar']['vevent']['summary']

    @property
    def start(self) -> datetime.datetime:
        timeobj = self._get_time('dtstart')

        timezone = timeobj.get('timezone')
        # TODO use the timezone
        time = self._parse_date_time(timeobj['time'])
        if time is None:
            return datetime.datetime.max
        return time

    @property
    def end(self) -> datetime.datetime:
        timeobj = self._get_time('dtend')
        if timeobj is None:
            return datetime.datetime.min

        time = self._parse_date_time(timeobj['time'])

        if time is None:
            return datetime.datetime.min
        return time

    def _get_time(self, name: str) -> Optional[Dict[str, str]]:
        return self._event['vcalendar']['vevent'].get(name)

    @staticmethod
    def _parse_date_time(raw_datetime: str) -> Optional[datetime.datetime]:
        """Parse datetime in format 'YYYYMMDDTHHmmss'"""
        if not raw_datetime:
            return None

        year = raw_datetime[:4]
        month = raw_datetime[4:6]
        day = raw_datetime[6:8]

        hours = raw_datetime[9:11]
        minutes = raw_datetime[11:13]
        seconds = raw_datetime[13:15]

        try:
            return datetime.datetime(year=int(year), month=int(month), day=int(day),
                                     hour=int(hours), minute=int(minutes), second=int(seconds))
        except ValueError as e:
            _LOGGER.warning(f"Could not parse {raw_datetime}. ")
            return None
