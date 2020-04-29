import voluptuous as vol
import logging
import pytz

from datetime import timedelta, time, date, datetime
from dateutil.relativedelta import relativedelta
from etesync import Authenticator, EteSync
from typing import Optional, Dict, List, Tuple, Generator

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

from .helpers import parse, parse_iso8601_duration, read_from_cache, write_to_cache

DOMAIN = 'etesync_calendar'

PARALLEL_UPDATES = 1

CONF_ENCRYPTION_PASSWORD = 'encryption_password'
CONF_DEFAULT_TIMEZONE = 'default_timezone'
CACHE_FOLDER = 'custom_components/etesync_calendar/cache'

CALENDAR_ITEM_TYPE = 'CALENDAR'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_URL): vol.Url(),
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_ENCRYPTION_PASSWORD): cv.string,
        vol.Optional(CONF_DEFAULT_TIMEZONE, default='Europe/Amsterdam'): cv.string,
        # vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
    }
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEZONE = ''


def setup_platform(hass, config, add_entities, discovery_info=None):
    url = config[CONF_URL]
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]
    encryption_password = config[CONF_ENCRYPTION_PASSWORD]

    global DEFAULT_TIMEZONE
    DEFAULT_TIMEZONE = config[CONF_DEFAULT_TIMEZONE]

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

    journals = list(ete_sync.list())
    _LOGGER.info("Journals found: %s", str(len(journals)))
    devices = []

    for journal in journals:
        # Filter task list / address book's
        if journal.info['type'] == CALENDAR_ITEM_TYPE:
            name = f"{username}-{journal.info['displayName']}"
            entity_id = generate_entity_id(ENTITY_ID_FORMAT, name, hass=hass)
            device = EteSyncCalendarEventDevice(hass, journal, ete_sync, entity_id)
            devices.append(device)
    add_entities(devices, True)


def _credentials_not_changed(old, new) -> bool:
    """Returns true if the first 3 values of old and new are not equal."""
    for i in range(3):
        if not old[i] == new[i]:
            _LOGGER.warning("credentials have changed!")
            return False
    return True


def add_timezone(dt: datetime, tz: Optional[str]) -> datetime:
    """Add the given tz timezone to the datetime and return the result"""

    if tz is None or tz.lower() == 'date':
        return pytz.timezone(DEFAULT_TIMEZONE).localize(dt)

    if dt is not None and tz is not None:
        return pytz.timezone(tz).localize(dt)


class EteSyncCalendarEventDevice(CalendarEventDevice):
    """A device for a single etesync calendar."""

    def __init__(self, hass, calendar, ete_sync, entity_id):
        self._hass = hass
        self._calendar = EteSyncCalendar(calendar, ete_sync)
        self._entity_id = entity_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._calendar.name

    @property
    def event(self) -> "EteSyncEvent":
        """Returns the closest upcoming or current event."""
        return self._calendar.next_event

    @property
    def state_attributes(self):
        event = self.event
        if event is None:
            return None
        return {
            "id": event.id,
            "message": event.summary,
            "all_day": event.is_all_day,
            "start_time": event.start,
            "end_time": event.end,
            "location": None,
            "description": event.description,
        }

    @property
    def state(self):
        """Return the state of the calendar event."""
        event = self.event
        if event is None:
            return STATE_OFF

        now = datetime.now().astimezone()

        if event.datetime_in_event(now):
            return STATE_ON
        return STATE_OFF

    async def async_get_events(self, hass, start_date, end_date):
        return self._calendar.get_events_in_range(start_date, end_date)

    def update(self):
        self._calendar.update()


class EteSyncCalendar:
    """Class that represents an etesync calendar."""

    def __init__(self, raw_data, ete_sync: "EteSync"):
        """Initialize the EteSyncCalendar class."""
        self._raw_data = raw_data
        self._ete_sync = ete_sync
        self._event_descriptions: List[EteSyncEventDescription] = []
        self._build_events()

    def _build_events(self):
        events = self._raw_data.collection.list()
        for event in events:
            self._event_descriptions.append(EteSyncEventDescription(event))
        # self._event_descriptions.sort(key=lambda e: e.start)

    def get_events_in_range(self, start_date: datetime, end_date: datetime):
        """Return calendar events within a datetime range."""
        events = []
        for event_description in self._event_descriptions:
            for event in event_description.events():

                if event.is_in_range(start_date, end_date):
                    events.append(event_description)

                if event.start > end_date:
                    break
        return events

    @property
    def name(self):
        """Return the name of the Calendar"""
        return self._raw_data.info['displayName']

    @property
    def next_event(self):
        """Returns the closest upcoming or current event."""
        the_next_event = None
        delta = timedelta.max

        now = datetime.now().astimezone()
        for event_description in self._event_descriptions:
            for event in event_description.events():
                event_delta, event_is_in_future = event.delta(now)

                if event_is_in_future:
                    if event_delta < delta:
                        the_next_event = event
                        delta = event_delta

                if event.start > now:
                    break

        return the_next_event

    @Throttle(timedelta(minutes=5))
    def update(self):
        """Update the calendar data"""
        self._ete_sync.sync()
        # TODO update data
        self._raw_data = self._ete_sync.get(self._raw_data.uid)
        self._build_events()


class EteSyncEventDescription:

    def __init__(self, event_data):
        self._raw_data = event_data
        raw_properties = event_data.content.splitlines()
        properties = []

        for line in raw_properties:
            key_value = line.split(':', 1)
            properties.append(key_value)

        self._event = parse(properties)

    def update(self, new_data):
        """Update event description with new data if anything has changed"""
        pass

    def events(self) -> Generator["EteSyncEvent", None, None]:
        """Generator for the one or more events this description describes."""

        id, summary, description, is_all_day = self._get_generic_event_properties()

        if self._is_recurring():
            start = self._start()
            end = self._end()
            interval = self._interval()
            duration = self._duration()

            while start < end:
                yield EteSyncEvent(id, summary, description, start, duration, is_all_day)
                start = start + interval
        else:
            yield EteSyncEvent(id, summary, description, self._start(), self._end() - self._start(), is_all_day)

    def _get_generic_event_properties(self):
        id = self._event['vcalendar']['vevent']['uid']
        summary = self._event['vcalendar']['vevent'].get('summary', '')
        description = self._event['vcalendar']['vevent'].get('description', '')
        is_all_day = self._is_all_day()

        return id, summary, description, is_all_day

    def _is_all_day(self):
        start = self._start()
        end = self._end()
        duration = self._duration()
        if end is None or self._is_recurring():
            # 60 * 60 * 24 = 86400 seconds a day
            return duration.total_seconds() > 86399
        return not self._is_recurring and (start - end).total_seconds() > 86399

    def _is_recurring(self) -> bool:
        return self._event['vcalendar']['vevent'].get('rrule') is not None

    def _duration(self) -> Optional[timedelta]:
        duration_text = self._event['vcalendar']['vevent'].get('duration')
        return parse_iso8601_duration(duration_text)

    def _start(self) -> datetime:
        """Returns the start datetime of the Event or datetime.max if none."""
        timeobj = self._get_time('dtstart')

        timezone = timeobj.get('timezone')
        parsed_time = self._parse_date_time(timeobj['time'], timezone)

        if parsed_time is None:
            return add_timezone(datetime.min, 'utc')
        return parsed_time

    def _end(self) -> datetime:
        """Returns the end datetime of the Event or datetime.min if none.
            If it is an all day event, will return datetime.date + time.max.
        """
        # the endtime might not be specified on a full day event
        timeobj = self._get_time('dtend')
        if timeobj is None:
            start = self._start()
            if start is not None and start.time == time.min:
                return datetime.combine(start.date(), time.max, start.tzinfo)
            else:
                return add_timezone(datetime.max, 'utc')

        timezone = timeobj.get('timezone')
        parsed_time = self._parse_date_time(timeobj['time'], timezone)

        if parsed_time is None:
            return add_timezone(datetime.max, 'utc')
        return parsed_time

    def _interval(self) -> relativedelta:
        frequency = self._event['vcalendar']['vevent']['rrule']['freq']

        if frequency == 'daily':
            return relativedelta(days=1)
        elif frequency == 'weekly':
            return relativedelta(weeks=1)
        elif frequency == 'monthly':
            return relativedelta(months=1)
        elif frequency == 'yearly':
            return relativedelta(years=1)
        else:
            _LOGGER.warning('Interval not yet supported %s', frequency)
            return None

    def _get_time(self, name: str) -> Optional[Dict[str, str]]:
        """Read the time form the raw data."""
        return self._event['vcalendar']['vevent'].get(name)

    @staticmethod
    def _parse_date_time(raw_datetime: str, timezone: str) -> Optional[datetime]:
        """Parse datetime in format 'YYYYMMDDTHHmmss'"""
        if not raw_datetime:
            return None

        year = raw_datetime[:4]
        month = raw_datetime[4:6]
        day = raw_datetime[6:8]

        hours = raw_datetime[9:11]
        minutes = raw_datetime[11:13]
        seconds = raw_datetime[13:15]

        if hours == '' or minutes == '' or seconds == '':
            dt = datetime.combine(date(year=int(year), month=int(month), day=int(day)),
                                           time.min)
        else:
            dt = datetime(year=int(year), month=int(month), day=int(day),
                                   hour=int(hours), minute=int(minutes), second=int(seconds))

        return add_timezone(dt, timezone)


class EteSyncEvent:
    """Class that represents an event."""

    def __init__(self, event_id: str,
                 summary: str,
                 description: str,
                 start: datetime,
                 duration: timedelta,
                 is_all_day=False) -> None:
        """Initialize the EteSyncEvent class."""
        self._id = event_id
        self._summary = summary
        self._description = description
        self._start = start
        self._duration = duration
        self._is_all_day = is_all_day

    @property
    def id(self) -> str:
        """Returns the Event id."""
        return self._id

    @property
    def summary(self) -> str:
        """Returns the event summary."""
        return self._summary

    @property
    def description(self) -> str:
        """Returns the event description."""
        return self._description

    @property
    def start(self) -> datetime:
        """Returns the start datetime of the Event or datetime.max if none."""
        return self._start

    @property
    def end(self) -> datetime:
        """Returns the end datetime of the Event or datetime.min if none.
            If it is an all day event, will return datetime.date + time.max.
        """
        return self.start + self.duration

    @property
    def is_all_day(self) -> bool:
        return self._is_all_day

    @property
    def duration(self) -> timedelta:
        """
        :return: The duration as timedelta
        """
        return self._duration

    def datetime_in_event(self, dt: datetime) -> bool:
        """
        Check if a given datetime falls in the event.
        :param dt: The datetime the event is compared against.
        :return: True if the given dt falls in the event.
        """
        start = self.start
        end = self.end

        if start is None or end is None:
            return False

        if start <= dt < end:
            return True
        return False

    def delta(self, dt: datetime) -> Tuple[timedelta, bool]:
        """
        :param dt: The datetime relative to the event
        :return: The timedelta between the given dt and the event or a timedelta of 0 if the dt falls in the event.
        """
        if self.datetime_in_event(dt):
            return timedelta(0), True

        if self.start > dt:
            return self.start - dt, True
        end = self.end
        return end - dt, end > dt

    def is_in_range(self, start_date: datetime, end_date: datetime) -> bool:
        """
        returns true if the event occurs in between the given start and end dates.
        This includes events that only partially overlap the given range.
        """
        return self.start > end_date and self.end < start_date
