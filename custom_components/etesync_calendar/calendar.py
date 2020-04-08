import voluptuous as vol
import logging
import datetime
import pytz

from dateutil.relativedelta import relativedelta
from etesync import Authenticator, EteSync
from typing import Optional, Dict, List

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


def setup_platform(hass, config, add_entities, disc_info=None):
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

    journals = ete_sync.list()

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


def add_timezone(dt: datetime.datetime, tz: Optional[str]) -> datetime.datetime:
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

        now = datetime.datetime.now().astimezone()

        if event.datetime_in_event(now):
            return True
        return False

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
        self._events: List[EteSyncEvent] = []
        self._build_events()

    def _build_events(self):
        events = self._raw_data.collection.list()
        for event in events:
            self._events.append(EteSyncEvent(event))
        self._events.sort(key=lambda e: e.start)

    def get_events_in_range(self, start_date: datetime.datetime, end_date: datetime.datetime):
        """Return calendar events within a datetime range."""

        events = []
        for event in self._events:
            if event.is_in_range(start_date, end_date):
                events.append(event)
        return events

    @property
    def name(self):
        """Return the name of the Calendar"""
        return self._raw_data.info['displayName']

    @property
    def next_event(self):
        """Returns the closest upcoming or current event."""
        the_next_event = None
        delta = datetime.timedelta.max

        now = datetime.datetime.now().astimezone()
        for event in self._events:
            d = event.delta(now)

            if d.seconds == 0:
                # Event in progress
                return event
            elif d.seconds > 0:
                # Event in the future
                if d < delta:
                    the_next_event = event
                    delta = d

        return the_next_event

    @Throttle(datetime.timedelta(minutes=5))
    def update(self):
        """Update the calendar data"""
        self._ete_sync.sync()
        # TODO update data
        self._raw_data = self._ete_sync.get(self._raw_data.uid)
        self._build_events()


class EteSyncEvent:
    """Class that represents an etesync event."""

    def __init__(self, event):
        """Initialize the EteSyncEvent class."""
        self._raw_event = event
        raw_properties = event.content.splitlines()
        properties = []

        for line in raw_properties:
            key_value = line.split(':', 1)
            properties.append(key_value)

        self._event = parse(properties)

    @property
    def id(self) -> str:
        """Returns the Event id."""
        return self._event['vcalendar']['vevent']['uid']

    @property
    def summary(self) -> str:
        """Returns the event summary."""
        return self._event['vcalendar']['vevent'].get('summary', '')

    @property
    def description(self) -> str:
        """Returns the event description."""
        return self._event['vcalendar']['vevent'].get('description', '')

    @property
    def is_recurring(self) -> bool:
        return self._event['vcalendar']['vevent'].get('rrule') is not None

    @property
    def start(self) -> datetime.datetime:
        """Returns the start datetime of the Event or datetime.max if none."""
        timeobj = self._get_time('dtstart')

        timezone = timeobj.get('timezone')
        time = self._parse_date_time(timeobj['time'], timezone, True)

        if time is None:
            return add_timezone(datetime.datetime.max, 'utc')
        return time

    @property
    def end(self) -> datetime.datetime:
        """Returns the end datetime of the Event or datetime.min if none.
            If it is an all day event, will return datetime.date + time.max.
        """
        # the endtime might not be specified on a full day event
        timeobj = self._get_time('dtend')
        if timeobj is None:
            start = self.start
            if start is not None and start.time == datetime.time.min:
                return datetime.datetime.combine(start.date(), datetime.time.max, start.tzinfo)
            else:
                return add_timezone(datetime.datetime.min, 'utc')

        timezone = timeobj.get('timezone')
        time = self._parse_date_time(timeobj['time'], timezone, False)

        if time is None:
            return add_timezone(datetime.datetime.min, 'utc')
        return time

    @property
    def duration(self) -> datetime.timedelta:
        """
        :return: The duration as timedelta
        """
        duration_text = self._event['vcalendar']['vevent'].get('duration')
        if duration_text is not None:
            return self._parse_duration(duration_text)

    @property
    def is_all_day(self) -> bool:
        """Returns true if this is an all day event."""
        return self.start.time == datetime.time.min and self.end.time == datetime.time.max

    def datetime_in_event(self, dt: datetime.datetime) -> bool:
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

    def delta(self, dt: datetime.datetime) -> datetime.timedelta:
        """
        :param dt: The datetime relative to the event
        :return: The timedelta between the given dt and the event or a timedelta of 0 if the dt falls in the event.
        """
        if self.is_recurring:
            frequency = self._event['vcalendar']['vevent']['rrule']['freq']

            if frequency == 'daily':
                interval = datetime.timedelta(days=1)
            elif frequency == 'weekly':
                interval = datetime.timedelta(weeks=1)
            elif frequency == 'monthly':
                interval = relativedelta(months=1)
            elif frequency == 'yearly':
                interval = relativedelta(years=1)
            else:
                _LOGGER.warning('Interval not yet supported %s', frequency)
                return datetime.timedelta.min

            best_delta = datetime.timedelta.min
            start = self.start
            event_end = self.end
            duration = self.duration

            while start < dt:
                # TODO we are currently missing the first event, but this ensures that at least one event after
                # the dt is processed
                start = start + interval
                end = start + duration

                if end > dt:
                    return datetime.timedelta(0)

                delta_start = start - dt
                delta_end = end - dt

                delta = self.best_delta(delta_start, delta_end)
                best_delta = self.best_delta(best_delta, delta)

                if start > event_end:
                    break

            return best_delta

        if self.datetime_in_event(dt):
            return datetime.timedelta(0)

        if self.start < dt:
            return self.start - dt
        return self.end - dt

    @staticmethod
    def best_delta(left: datetime.timedelta, right: datetime.timedelta) -> datetime.timedelta:
        """
        The best delta is the delta closest to 0
        :param left: a timedelta
        :param right: a timedelta
        :return: the timedelta closest to 0
        """
        left_sec = left.seconds
        right_sec = right.seconds

        # if both are negative
        if left_sec < 0 and right_sec < 0:
            if left_sec > right_sec:
                return left
            else:
                return right

        # if both are positive
        if left_sec > 0 and right_sec > 0:
            if left_sec > right_sec:
                return right
            else:
                return left

        # return the only positive
        if left_sec > 0:
            return left
        return right

    def is_in_range(self, start_date: datetime.datetime, end_date: datetime.datetime) -> bool:
        """
        returns true if the event occurs in between the given start and end dates.
        This includes events that only partially overlap the given range.
        """
        if self.is_recurring:
            frequency = self._event['vcalendar']['vevent']['rrule']['freq']

            if frequency == 'daily':
                difference = end_date - start_date
                if difference.days > 1:
                    return True

                interval = datetime.timedelta(days=1)
            elif frequency == 'weekly':
                difference = end_date - start_date
                if difference.days > 7:
                    return True

                interval = datetime.timedelta(days=7)
            elif frequency == 'monthly':
                difference = end_date - start_date
                if difference.days > 31:
                    return True

                interval = relativedelta(months=1)
            elif frequency == 'yearly':
                difference = end_date - start_date
                if difference.days > 365:
                    return True

                interval = relativedelta(years=1)
            else:
                _LOGGER.warning('Interval not yet supported: %s', frequency)
                return False

            start = self.start
            duration = self.duration

            while start < end_date:
                end = start + duration
                if start > end_date and end < start_date:
                    return True
                start = start + interval
            return False
        else:
            return self.start > end_date and self.end < start_date

    def _get_time(self, name: str) -> Optional[Dict[str, str]]:
        """Read the time form the raw data."""
        return self._event['vcalendar']['vevent'].get(name)

    @staticmethod
    def _parse_date_time(raw_datetime: str, timezone: str, is_start=True) -> Optional[datetime.datetime]:
        """Parse datetime in format 'YYYYMMDDTHHmmss'"""
        if not raw_datetime:
            return None

        year = raw_datetime[:4]
        month = raw_datetime[4:6]
        day = raw_datetime[6:8]

        hours = raw_datetime[9:11]
        minutes = raw_datetime[11:13]
        seconds = raw_datetime[13:15]

        if hours == '' and minutes == '' and seconds == '':
            if is_start:
                dt = datetime.datetime.combine(datetime.date(year=int(year), month=int(month), day=int(day)),
                                               datetime.time.min)
            else:
                dt = datetime.datetime.combine(datetime.date(year=int(year), month=int(month), day=int(day)),
                                               datetime.time.max)
        else:
            dt = datetime.datetime(year=int(year), month=int(month), day=int(day),
                                   hour=int(hours), minute=int(minutes), second=int(seconds))

        return add_timezone(dt, timezone)

    @staticmethod
    def _parse_duration(duration_text: str) -> datetime.timedelta:
        """
        Parse an ISO 8601 duration into a timedelta
        https://en.wikipedia.org/wiki/ISO_8601#Durations
        example param: P3Y6M4DT12H30M5S
                       PT3600S

        :param duration_text: Duration as string in ISO 8601 format
        :return: datetime.timedelta based on duration_text param
        """
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
        return datetime.timedelta(days=total_days, seconds=total_seconds)
