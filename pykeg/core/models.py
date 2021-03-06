# -*- coding: latin-1 -*-
# Copyright 2010 Mike Wakerly <opensource@hoho.com>
#
# This file is part of the Pykeg package of the Kegbot project.
# For more information on Pykeg or Kegbot, see http://kegbot.org/
#
# Pykeg is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# Pykeg is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Pykeg.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import os
import random

from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.signals import post_save
from django.db.models.signals import pre_save
from django.contrib.sites.models import Site
from django.contrib.auth.models import User
from django.utils import timezone

from pykeg import EPOCH

from pykeg.core import kb_common
from pykeg.core import fields
from pykeg.core import imagespecs
from pykeg.core import jsonfield
from pykeg.core import managers
from pykeg.core import stats
from pykeg.core.util import make_serial

from kegbot.util import units
from kegbot.util import util

from kegbot.api import models_pb2
from kegbot.api import protoutil

"""Django models definition for the kegbot database."""

class KegbotSite(models.Model):
  name = models.CharField(max_length=64, unique=True, default='default',
      editable=False)
  is_active = models.BooleanField(default=True,
      help_text='On/off switch for this site.')
  is_setup = models.BooleanField(default=True,
      help_text='True if the site has completed setup.',
      editable=False)
  epoch = models.PositiveIntegerField(default=EPOCH,
      help_text='Database epoch number.',
      editable=False)
  serial_number = models.TextField(max_length=128, editable=False,
      blank=True, default='',
      help_text='A unique id for this system.')

  def __str__(self):
    return self.name

  def full_url(self):
    return 'http://%s' % Site.objects.get_current().domain

  def GetStatsRecord(self):
    try:
      return SystemStats.objects.get(site=self)
    except SystemStats.DoesNotExist:
      return None

  def GetStats(self):
    record = self.GetStatsRecord()
    if record:
      return record.stats
    return {}

def _kegbotsite_pre_save(sender, instance, **kwargs):
  if not instance.serial_number:
    instance.serial_number = make_serial()
pre_save.connect(_kegbotsite_pre_save, sender=KegbotSite)

def _kegbotsite_post_save(sender, instance, **kwargs):
  """Creates a SiteSettings object if none already exists."""
  settings, _ = SiteSettings.objects.get_or_create(site=instance)
post_save.connect(_kegbotsite_post_save, sender=KegbotSite)

class SiteSettings(models.Model):
  VOLUME_DISPLAY_UNITS_CHOICES = (
    ('metric', 'Metric (mL, L)'),
    ('imperial', 'Imperial (oz, pint)'),
  )
  TEMPERATURE_DISPLAY_UNITS_CHOICES = (
    ('f', 'Fahrenheit'),
    ('c', 'Celsius'),
  )
  PRIVACY_CHOICES = (
    ('public', 'Public: Browsing does not require login'),
    ('members', 'Members only: Must log in to browse'),
    ('staff', 'Staff only: Only logged-in staff accounts may browse'),
  )
  DEFAULT_PRIVACY = 'public'

  site = models.OneToOneField(KegbotSite, related_name='settings')
  volume_display_units = models.CharField(max_length=64,
      choices=VOLUME_DISPLAY_UNITS_CHOICES, default='imperial',
      help_text='Unit system to use when displaying volumetric data.')
  temperature_display_units = models.CharField(max_length=64,
      choices=TEMPERATURE_DISPLAY_UNITS_CHOICES, default='f',
      help_text='Unit system to use when displaying temperature data.')
  title = models.CharField(max_length=64, blank=True, null=True,
      help_text='The title of this site. Example: "Kegbot San Francisco"')
  description = models.TextField(blank=True, null=True,
      help_text='Description of this site')
  background_image = models.ForeignKey('Picture', blank=True, null=True,
      help_text='Background for this site.')
  event_web_hook = models.URLField(blank=True, null=True,
      help_text='Web hook URL for newly-generated events.')
  google_analytics_id = models.CharField(blank=True, null=True, max_length=64,
      help_text='Set to your Google Analytics ID to enable tracking. '
      'Example: UA-XXXX-y')
  session_timeout_minutes = models.PositiveIntegerField(
      default=kb_common.DRINK_SESSION_TIME_MINUTES,
      help_text='Maximum time, in minutes, that a session may be idle (no pours) '
          'before it is considered to be finished.  '
          'Recommended value is %s.' % kb_common.DRINK_SESSION_TIME_MINUTES)
  privacy = models.CharField(max_length=63, choices=PRIVACY_CHOICES,
      default=DEFAULT_PRIVACY,
      help_text='Whole-system setting for system privacy.')
  guest_name = models.CharField(max_length=63, default='guest',
      help_text='Name to be shown in various places for unauthenticated pours.')
  guest_image = models.ForeignKey('Picture', blank=True, null=True,
      related_name='guest_images',
      help_text='Profile picture to be shown for unauthenticated pours.')
  default_user = models.ForeignKey(User, blank=True, null=True,
      help_text='Default user to set as owner for unauthenticated drinks. '
          'When set, the "guest" user will not be used. This is mostly '
          'useful for closed / single user systems.')
  registration_allowed = models.BooleanField(default=True,
      help_text='Whether to allow new user registration.')
  registration_confirmation = models.BooleanField(default=False,
      help_text='Whether registration requires e-mail confirmation.')

  allowed_hosts = models.TextField(blank=True, null=True, default='',
      help_text='List of allowed hostnames. If blank, validation is disabled.')

  class Meta:
    verbose_name_plural = "site settings"

  def GetSessionTimeoutDelta(self):
    return datetime.timedelta(minutes=self.session_timeout_minutes)

  def base_url(self):
    return 'http://%s' % (Site.objects.get_current(),)

  def reverse_full(self, *args, **kwargs):
    """Calls reverse, and returns a full URL (includes base_url())."""
    return '%s%s' % (self.base_url(), reverse(*args, **kwargs))

  @classmethod
  def get(cls):
    """Gets the default site settings."""
    site = KegbotSite.objects.get(name='default')
    return site.settings


class UserProfile(models.Model):
  """Extra per-User information."""
  def __str__(self):
    return "profile for %s" % (self.user,)

  def FacebookProfile(self):
    if 'socialregistration' not in settings.INSTALLED_APPS:
      return None
    qs = self.user.facebookprofile_set.all()
    if qs:
      return qs[0]

  def TwitterProfile(self):
    if 'socialregistration' not in settings.INSTALLED_APPS:
      return None
    qs = self.user.twitterprofile_set.all()
    if qs:
      return qs[0]

  def GetStatsRecord(self):
    try:
      return UserStats.objects.get(user=self)
    except UserStats.DoesNotExist:
      return None

  def GetStats(self):
    record = self.GetStatsRecord()
    if record:
      return record.stats
    return {}

  def RecomputeStats(self):
    self.user.stats.all().delete()
    last_d = self.user.drinks.valid().order_by('-time')
    if last_d:
      last_d[0]._UpdateUserStats()

  def GetApiKey(self):
    api_key, new = ApiKey.objects.get_or_create(user=self.user,
        defaults={'key': ApiKey.generate_key()})
    return api_key.key

  @models.permalink
  def get_absolute_url(self):
    return ('kb-drinker', (self.user.username,))

  user = models.OneToOneField(User)
  mugshot = models.ForeignKey('Picture', blank=True, null=True)

def _user_post_save(sender, instance, **kwargs):
  profile, new = UserProfile.objects.get_or_create(user=instance)
post_save.connect(_user_post_save, sender=User)


class ApiKey(models.Model):
  user = models.OneToOneField(User)
  key = models.CharField(max_length=127, editable=False, unique=True)
  active = models.BooleanField(default=True)

  def is_active(self):
    """Returns true if both the key and the key's user are active."""
    return self.active and self.user.is_active

  def regenerate(self):
    self.key = self.generate_key()
    self.save()

  @classmethod
  def generate_key(cls):
    '''Returns a new random key.'''
    return '%032x' % random.randint(0, 2**128 - 1)


class BeerDBModel(models.Model):
  class Meta:
    abstract = True
  added = models.DateTimeField(default=timezone.now, editable=False)
  edited = models.DateTimeField(editable=False)
  beerdb_id = models.CharField(blank=True, null=True, max_length=128)

  def save(self, *args, **kwargs):
    self.edited = timezone.now()
    super(BeerDBModel, self).save(*args, **kwargs)


class Brewer(BeerDBModel):
  """Describes a producer of beer."""
  PRODUCTION_CHOICES = (
    ('commercial', 'Commercial brewer'),
    ('homebrew', 'Home brewer'),
  )

  name = models.CharField(max_length=255,
      help_text='Name of the brewer')
  country = fields.CountryField(default='USA',
      help_text='Country of origin')
  origin_state = models.CharField(max_length=128,
      default='', blank=True, null=True,
      help_text='State of origin, if applicable')
  origin_city = models.CharField(max_length=128, default='', blank=True,
      null=True,
      help_text='City of origin, if known')
  production = models.CharField(max_length=128, choices=PRODUCTION_CHOICES,
      default='commercial')
  url = models.URLField(default='', blank=True, null=True,
      help_text='Brewer\'s home page')
  description = models.TextField(default='', blank=True, null=True,
      help_text='A short description of the brewer')
  image = models.ForeignKey('Picture', blank=True, null=True,
      related_name='beer_brewers')

  def __str__(self):
    return self.name


class BeerStyle(BeerDBModel):
  """Describes a named style of beer (Stout, IPA, etc)"""
  name = models.CharField(max_length=128,
      help_text='Name of the beer style')

  def __str__(self):
    return self.name


class BeerType(BeerDBModel):
  """Describes a specific kind of beer, by name, brewer, and style."""
  name = models.CharField(max_length=255)
  brewer = models.ForeignKey(Brewer)
  style = models.ForeignKey(BeerStyle)

  edition = models.CharField(max_length=255, blank=True, null=True,
      help_text='For seasonal or special edition beers, enter the '
          'year or other edition name')

  abv = models.FloatField(blank=True, null=True,
      help_text='Alcohol by volume, as percentage (0-100), if known')
  calories_oz = models.FloatField(blank=True, null=True,
      help_text='Calories per ounce of beer, if known')
  carbs_oz = models.FloatField(blank=True, null=True,
      help_text='Carbohydrates per ounce of beer, if known')

  original_gravity = models.FloatField(blank=True, null=True,
      help_text='Original gravity of the beer, if known')
  specific_gravity = models.FloatField(blank=True, null=True,
      help_text='Specific/final gravity of the beer, if known')

  image = models.ForeignKey('Picture', blank=True, null=True,
      related_name='beer_types')

  untappd_beer_id = models.IntegerField(blank=True, null=True,
      help_text='Untappd.com beer id for this beer, if known')

  def __str__(self):
    return "%s by %s" % (self.name, self.brewer)

  def GetImage(self):
    if self.image:
      return self.image
    if self.brewer.image:
      return self.brewer.image
    return None


class KegSize(models.Model):
  """ A convenient table of common Keg sizes """
  def __str__(self):
    gallons = units.Quantity(self.volume_ml).InUSGallons()
    return "%s [%.2f gal]" % (self.name, gallons)

  name = models.CharField(max_length=128)
  volume_ml = models.FloatField()


class KegTap(models.Model):
  """A physical tap of beer."""
  site = models.ForeignKey(KegbotSite, related_name='taps')
  name = models.CharField(max_length=128,
      help_text='The display name for this tap. Example: Main Tap.')
  meter_name = models.CharField(max_length=128,
      help_text='The name of the flow meter reporting to this tap. '
      'Example: kegboard.flow0')
  relay_name = models.CharField(max_length=128, blank=True, null=True,
      help_text='If a relay is attached to this tap, give its '
      'name here. Example: kegboard.relay0')
  ml_per_tick = models.FloatField(default=(1000.0/2200.0),
      help_text='mL per flow meter tick.  Common values: 0.166666666667 '
      '(SwissFlow), 0.454545454545 (Vision 2000)')
  description = models.TextField(blank=True, null=True)
  current_keg = models.OneToOneField('Keg', blank=True, null=True,
      related_name='current_tap')
  max_tick_delta = models.PositiveIntegerField(default=100)
  temperature_sensor = models.ForeignKey('ThermoSensor', blank=True, null=True)

  def __str__(self):
    return "%s: %s" % (self.meter_name, self.name)

  def Temperature(self):
    if self.temperature_sensor:
      last_rec = self.temperature_sensor.thermolog_set.all().order_by('-time')
      if last_rec:
        return last_rec[0]
    return None


class Keg(models.Model):
  """ Record for each installed Keg. """
  @models.permalink
  def get_absolute_url(self):
    return ('kb-keg', (str(self.id),))

  def full_volume(self):
    return self.size.volume_ml

  def served_volume(self):
    drinks = Drink.objects.filter(keg__exact=self, status__exact='valid')
    total = 0
    for d in drinks:
      total += d.volume_ml
    return total

  def spilled_volume(self):
    return self.spilled_ml

  def remaining_volume(self):
    return self.full_volume() - self.served_volume() - self.spilled_volume()

  def percent_full(self):
    result = float(self.remaining_volume()) / float(self.full_volume()) * 100
    result = max(min(result, 100), 0)
    return result

  def end_keg(self):
    if self.current_tap:
      tap = self.current_tap
      tap.current_keg = None
      tap.save()
    if self.status != 'offline':
      self.status = 'offline'
      self.save()

  def keg_age(self):
    if self.status == 'online':
      end = timezone.now()
    else:
      end = self.end_time
    return end - self.start_time

  def is_empty(self):
    return float(self.remaining_volume()) <= 0

  def is_active(self):
    return self.status == 'online'

  def previous(self):
    q = Keg.objects.filter(site=self.site, start_time__lt=self.start_time).order_by('-start_time')
    if q.count():
      return q[0]
    return None

  def next(self):
    q = Keg.objects.filter(site=self.site, start_time__gt=self.start_time).order_by('start_time')
    if q.count():
      return q[0]
    return None

  def GetStatsRecord(self):
    try:
      return KegStats.objects.get(keg=self)
    except KegStats.DoesNotExist:
      return None

  def GetStats(self):
    record = self.GetStatsRecord()
    if record:
      return record.stats
    return {}

  def RecomputeStats(self):
    self.stats.all().delete()
    last_d = self.drinks.valid().order_by('-start_time')
    if last_d:
      last_d[0]._UpdateKegStats()

  def Sessions(self):
    chunks = SessionChunk.objects.filter(keg=self).order_by('-start_time').select_related(depth=2)
    sessions = []
    sess = None
    for c in chunks:
      # Skip same sessions
      if c.session == sess:
        continue
      sess = c.session
      sessions.append(sess)
    return sessions

  def TopDrinkers(self):
    stats = self.GetStats()
    if not stats:
      return []
    ret = []
    entries = stats.get('volume_by_drinker', [])
    for entry in entries:
      username = str(entry.username)
      vol = entry.volume_ml
      try:
        user = User.objects.get(username=username)
      except User.DoesNotExist:
        continue  # should not happen
      ret.append((vol, user))
    ret.sort(reverse=True)
    return ret

  def __str__(self):
    return "Keg #%s - %s" % (self.id, self.type)

  site = models.ForeignKey(KegbotSite, related_name='kegs')
  type = models.ForeignKey('BeerType')
  size = models.ForeignKey(KegSize)
  start_time = models.DateTimeField(default=timezone.now)
  end_time = models.DateTimeField(default=timezone.now)
  status = models.CharField(max_length=128, choices=(
     ('online', 'online'),
     ('offline', 'offline'),
     ('coming soon', 'coming soon')))
  description = models.CharField(max_length=256, blank=True, null=True)
  origcost = models.FloatField(default=0, blank=True, null=True)
  spilled_ml = models.FloatField(default=0)
  notes = models.TextField(blank=True, null=True,
      help_text='Private notes about this keg, viewable only by admins.')

def _keg_pre_save(sender, instance, **kwargs):
  keg = instance
  # We don't need to do anything if the keg is still online.
  if keg.status != 'offline':
    return

  # Determine first drink date & set keg start date to it if earlier.
  drinks = keg.drinks.valid().order_by('time')
  if drinks:
    drink = drinks[0]
    if drink.time < keg.start_time:
      keg.start_time = drink.time

  # Determine last drink date & set keg end date to it if later.
  drinks = keg.drinks.valid().order_by('-time')
  if drinks:
    drink = drinks[0]
    if drink.time > keg.end_time:
      keg.end_time = drink.time

pre_save.connect(_keg_pre_save, sender=Keg)

def _keg_post_save(sender, instance, **kwargs):
  keg = instance
  SystemEvent.ProcessKeg(keg)

post_save.connect(_keg_post_save, sender=Keg)


class Drink(models.Model):
  """ Table of drinks records """
  class Meta:
    get_latest_by = 'time'
    ordering = ('-time',)

  @models.permalink
  def get_absolute_url(self):
    return ('kb-drink', (str(self.id),))

  def ShortUrl(self):
    return '%s%s' % (self.site.full_url(), reverse('kb-drink-short', args=(str(self.id),)))

  def Volume(self):
    return units.Quantity(self.volume_ml)

  def calories(self):
    if not self.keg or not self.keg.type:
      return 0
    ounces = self.Volume().InOunces()
    return self.keg.type.calories_oz * ounces

  def __str__(self):
    return "Drink %s:%i by %s" % (self.site.name, self.id, self.user)

  def _UpdateSystemStats(self):
    stats, created = SystemStats.objects.get_or_create(site=self.site)
    stats.Update(self)

  def _UpdateUserStats(self):
    if self.user:
      stats, created = UserStats.objects.get_or_create(user=self.user, site=self.site)
      stats.Update(self)

  def _UpdateKegStats(self):
    if self.keg:
      stats, created = KegStats.objects.get_or_create(keg=self.keg, site=self.site)
      stats.Update(self)

  def _UpdateSessionStats(self):
    if self.session:
      stats, created = SessionStats.objects.get_or_create(session=self.session, site=self.site)
      stats.Update(self)

  def PostProcess(self):
    self._UpdateSystemStats()
    self._UpdateUserStats()
    self._UpdateKegStats()
    self._UpdateSessionStats()
    SystemEvent.ProcessDrink(self)

  objects = managers.DrinkManager()

  site = models.ForeignKey(KegbotSite, related_name='drinks')

  # Ticks records the actual meter reading, which is never changed once
  # recorded.
  ticks = models.PositiveIntegerField(editable=False)

  # Volume is the actual volume of the drink.  Its initial value is a function
  # of `ticks`, but it may be adjusted, eg due to calibration or mis-recording.
  volume_ml = models.FloatField()

  time = models.DateTimeField()
  duration = models.PositiveIntegerField(blank=True, default=0)
  user = models.ForeignKey(User, null=True, blank=True, related_name='drinks')
  keg = models.ForeignKey(Keg, null=True, blank=True, related_name='drinks')
  status = models.CharField(max_length=128, choices = (
     ('valid', 'valid'),
     ('invalid', 'invalid'),
     ('deleted', 'deleted'),
     ), default = 'valid')
  session = models.ForeignKey('DrinkingSession',
      related_name='drinks', null=True, blank=True, editable=False)
  shout = models.TextField(blank=True, null=True,
      help_text='Comment from the drinker at the time of the pour.')
  tick_time_series = models.TextField(blank=True, null=True, editable=False,
      help_text='Tick update sequence that generated this drink')


class AuthenticationToken(models.Model):
  """A secret token to authenticate a user, optionally pin-protected."""
  class Meta:
    unique_together = ('auth_device', 'token_value')

  def __str__(self):
    auth_device = self.auth_device
    if auth_device == 'core.rfid':
      auth_device = 'RFID'
    elif auth_device == 'core.onewire':
      auth_device = 'OneWire'

    ret = "%s %s" % (auth_device, self.token_value)
    if self.nice_name:
      ret += " (%s)" % self.nice_name
    return ret

  site = models.ForeignKey(KegbotSite, related_name='tokens')
  auth_device = models.CharField(max_length=64)
  token_value = models.CharField(max_length=128)
  nice_name = models.CharField(max_length=256, blank=True, null=True,
      help_text='A human-readable alias for the token (eg "Guest Key").')
  pin = models.CharField(max_length=256, blank=True, null=True)
  user = models.ForeignKey(User, blank=True, null=True,
      related_name='tokens')
  enabled = models.BooleanField(default=True)
  created_time = models.DateTimeField(auto_now_add=True)
  expire_time = models.DateTimeField(blank=True, null=True)

  def get_auth_device(self):
    auth_device = self.auth_device
    if auth_device == 'core.rfid':
      auth_device = 'RFID'
    elif auth_device == 'core.onewire':
      auth_device = 'OneWire'
    return auth_device

  def IsAssigned(self):
    return self.user is not None

  def IsActive(self):
    if not self.enabled:
      return False
    if not self.expire_time:
      return True
    return timezone.now() < self.expire_time

def _auth_token_pre_save(sender, instance, **kwargs):
  if instance.auth_device in kb_common.AUTH_MODULE_NAMES_HEX_VALUES:
    instance.token_value = instance.token_value.lower()

pre_save.connect(_auth_token_pre_save, sender=AuthenticationToken)

class _AbstractChunk(models.Model):
  class Meta:
    abstract = True
    get_latest_by = 'start_time'
    ordering = ('-start_time',)

  start_time = models.DateTimeField()
  end_time = models.DateTimeField()
  volume_ml = models.FloatField(default=0)

  def Duration(self):
    return self.end_time - self.start_time

  def _AddDrinkNoSave(self, drink):
    session_delta = drink.site.settings.GetSessionTimeoutDelta()
    session_end = drink.time + session_delta

    if self.start_time > drink.time:
      self.start_time = drink.time
    if self.end_time < session_end:
      self.end_time = session_end
    self.volume_ml += drink.volume_ml

  def AddDrink(self, drink):
    self._AddDrinkNoSave(drink)
    self.save()


class DrinkingSession(_AbstractChunk):
  """A collection of contiguous drinks. """
  class Meta:
    get_latest_by = 'start_time'
    ordering = ('-start_time',)

  objects = managers.SessionManager()
  site = models.ForeignKey(KegbotSite, related_name='sessions')
  name = models.CharField(max_length=256, blank=True, null=True)

  def __str__(self):
    return "Session #%s: %s" % (self.id, self.start_time)

  def HighlightPicture(self):
    pictures = self.pictures.all().order_by('-time')
    if pictures:
      return pictures[0]
    chunks = self.user_chunks.filter(user__ne=None).order_by('-volume_ml')
    if chunks:
      mugshot = chunks[0].user.get_profile().mugshot
      return mugshot

  def OtherPictures(self):
    pictures = self.pictures.all().order_by('-time')
    if pictures:
      return pictures[1:]
    return []

  def RecomputeStats(self):
    self.stats.all().delete()
    try:
      last_d = self.drinks.valid().latest()
      last_d._UpdateSessionStats()
    except Drink.DoesNotExist:
      pass

  @models.permalink
  def get_absolute_url(self):
    dt = self.start_time
    if settings.USE_TZ:
      dt = timezone.localtime(dt)
    return ('kb-session-detail', (), {
      'year' : dt.year,
      'month' : dt.month,
      'day' : dt.day,
      'pk' : self.pk})

  def GetStatsRecord(self):
    try:
      return SessionStats.objects.get(session=self)
    except SessionStats.DoesNotExist:
      return None

  def GetStats(self):
    record = self.GetStatsRecord()
    if record:
      return record.stats
    return {}

  def summarize_drinkers(self):
    def fmt(user):
      url = '/drinkers/%s/' % (user.username,)
      return '<a href="%s">%s</a>' % (url, user.username)
    chunks = self.user_chunks.all().order_by('-volume_ml')
    users = tuple(c.user for c in chunks)
    names = tuple(fmt(u) for u in users if u)

    if None in users:
      guest_trailer = ' (and possibly others)'
    else:
      guest_trailer = ''

    num = len(names)
    if num == 0:
      return 'no known drinkers'
    elif num == 1:
      ret = names[0]
    elif num == 2:
      ret = '%s and %s' % names
    elif num == 3:
      ret = '%s, %s and %s' % names
    else:
      if guest_trailer:
        return '%s, %s and at least %i others' % (names[0], names[1], num-2)
      else:
        return '%s, %s and %i others' % (names[0], names[1], num-2)

    return '%s%s' % (ret, guest_trailer)

  def GetTitle(self):
    if self.name:
      return self.name
    else:
      if self.id:
        return 'Session %s' % (self.id,)
      else:
        # Not yet saved.
        return 'New Session'

  def AddDrink(self, drink):
    super(DrinkingSession, self).AddDrink(drink)
    session_delta = drink.site.settings.GetSessionTimeoutDelta()

    defaults = {
      'start_time': drink.time,
      'end_time': drink.time + session_delta,
    }

    # Update or create a SessionChunk.
    chunk, created = SessionChunk.objects.get_or_create(session=self,
        user=drink.user, keg=drink.keg, defaults=defaults)
    chunk.AddDrink(drink)

    # Update or create a UserSessionChunk.
    chunk, created = UserSessionChunk.objects.get_or_create(session=self,
        site=drink.site, user=drink.user, defaults=defaults)
    chunk.AddDrink(drink)

    # Update or create a KegSessionChunk.
    chunk, created = KegSessionChunk.objects.get_or_create(session=self,
        site=drink.site, keg=drink.keg, defaults=defaults)
    chunk.AddDrink(drink)

  def UserChunksByVolume(self):
    chunks = self.user_chunks.all().order_by('-volume_ml')
    return chunks

  def IsActiveNow(self):
    return self.IsActive(timezone.now())

  def IsActive(self, now):
    return self.end_time > now

  def Rebuild(self):
    self.volume_ml = 0
    self.chunks.all().delete()
    self.user_chunks.all().delete()
    self.keg_chunks.all().delete()

    drinks = self.drinks.valid()
    if not drinks:
      # TODO(mikey): cancel/delete the session entirely.  As it is, session will
      # remain a placeholder.
      return

    session_delta = self.site.settings.GetSessionTimeoutDelta()
    min_time = None
    max_time = None
    for d in drinks:
      self.AddDrink(d)
      if min_time is None or d.time < min_time:
        min_time = d.time
      if max_time is None or d.time > max_time:
        max_time = d.time
    self.start_time = min_time
    self.end_time = max_time + session_delta
    self.save()

  @classmethod
  def AssignSessionForDrink(cls, drink):
    # Return existing session if already assigned.
    if drink.session:
      return drink.session

    # Return last session if one already exists
    q = drink.site.sessions.all().order_by('-end_time')[:1]
    if q and q[0].IsActive(drink.time):
      session = q[0]
      session.AddDrink(drink)
      drink.session = session
      drink.save()
      return session

    # Create a new session
    session = cls(start_time=drink.time, end_time=drink.time,
        site=drink.site)
    session.save()
    session.AddDrink(drink)
    drink.session = session
    drink.save()
    return session


class SessionChunk(_AbstractChunk):
  """A specific user and keg contribution to a session."""
  class Meta:
    unique_together = ('session', 'user', 'keg')
    get_latest_by = 'start_time'
    ordering = ('-start_time',)

  session = models.ForeignKey(DrinkingSession, related_name='chunks')
  user = models.ForeignKey(User, related_name='session_chunks', blank=True,
      null=True)
  keg = models.ForeignKey(Keg, related_name='session_chunks', blank=True,
      null=True)


class UserSessionChunk(_AbstractChunk):
  """A specific user's contribution to a session (spans all kegs)."""
  class Meta:
    unique_together = ('session', 'user')
    get_latest_by = 'start_time'
    ordering = ('-start_time',)

  site = models.ForeignKey(KegbotSite, related_name='user_chunks')
  session = models.ForeignKey(DrinkingSession, related_name='user_chunks')
  user = models.ForeignKey(User, related_name='user_session_chunks', blank=True,
      null=True)

  def GetTitle(self):
    return self.session.GetTitle()

  def GetDrinks(self):
    return self.session.drinks.filter(user=self.user).order_by('time')


class KegSessionChunk(_AbstractChunk):
  """A specific keg's contribution to a session (spans all users)."""
  class Meta:
    unique_together = ('session', 'keg')
    get_latest_by = 'start_time'
    ordering = ('-start_time',)

  objects = managers.SessionManager()
  site = models.ForeignKey(KegbotSite, related_name='keg_chunks')
  session = models.ForeignKey(DrinkingSession, related_name='keg_chunks')
  keg = models.ForeignKey(Keg, related_name='keg_session_chunks', blank=True,
      null=True)

  def GetTitle(self):
    return self.session.GetTitle()


class ThermoSensor(models.Model):
  site = models.ForeignKey(KegbotSite, related_name='thermosensors')
  raw_name = models.CharField(max_length=256)
  nice_name = models.CharField(max_length=128)

  def __str__(self):
    if self.nice_name:
      return '%s (%s)' % (self.nice_name, self.raw_name)
    return self.raw_name

  def LastLog(self):
    try:
      return self.thermolog_set.latest()
    except Thermolog.DoesNotExist:
      return None


class Thermolog(models.Model):
  """ A log from an ITemperatureSensor device of periodic measurements. """
  class Meta:
    get_latest_by = 'time'
    ordering = ('-time',)

  site = models.ForeignKey(KegbotSite, related_name='thermologs')
  sensor = models.ForeignKey(ThermoSensor)
  temp = models.FloatField()
  time = models.DateTimeField()

  def __str__(self):
    return '%s %.2f C / %.2f F [%s]' % (self.sensor, self.TempC(),
        self.TempF(), self.time)

  def TempC(self):
    return self.temp

  def TempF(self):
    return util.CtoF(self.temp)


class _StatsModel(models.Model):
  STATS_BUILDER = None

  class Meta:
    abstract = True

  def Update(self, drink, force=False):
    previous = None
    try:
      if not force and self.stats:
        previous = protoutil.DictToProtoMessage(self.stats, models_pb2.Stats())
    except TypeError, e:
      pass
    builder = self.STATS_BUILDER(drink, previous)
    self.stats = protoutil.ProtoMessageToDict(builder.Build())
    self.save()

  site = models.ForeignKey(KegbotSite)
  time = models.DateTimeField(default=timezone.now)
  stats = jsonfield.JSONField()


class SystemStats(_StatsModel):
  STATS_BUILDER = stats.SystemStatsBuilder

  def __str__(self):
    return 'SystemStats for %s' % self.site


class UserStats(_StatsModel):
  class Meta:
    unique_together = ('site', 'user')
  STATS_BUILDER = stats.DrinkerStatsBuilder
  user = models.ForeignKey(User, blank=True, null=True, related_name='stats')

  def __str__(self):
    return 'UserStats for %s' % self.user


class KegStats(_StatsModel):
  STATS_BUILDER = stats.KegStatsBuilder
  keg = models.ForeignKey(Keg, unique=True, related_name='stats')
  completed = models.BooleanField(default=False)

  def __str__(self):
    return 'KegStats for %s' % self.keg


class SessionStats(_StatsModel):
  STATS_BUILDER = stats.SessionStatsBuilder
  session = models.ForeignKey(DrinkingSession, unique=True, related_name='stats')
  completed = models.BooleanField(default=False)

  def __str__(self):
    return 'SessionStats for %s' % self.session


class SystemEvent(models.Model):
  class Meta:
    ordering = ('-id',)
    get_latest_by = 'time'

  KINDS = (
      ('drink_poured', 'Drink poured'),
      ('session_started', 'Session started'),
      ('session_joined', 'User joined session'),
      ('keg_tapped', 'Keg tapped'),
      ('keg_ended', 'Keg ended'),
  )

  site = models.ForeignKey(KegbotSite, related_name='events')
  kind = models.CharField(max_length=255, choices=KINDS,
      help_text='Type of event.')
  time = models.DateTimeField(help_text='Time of the event.')
  user = models.ForeignKey(User, blank=True, null=True,
      related_name='events',
      help_text='User responsible for the event, if any.')
  drink = models.ForeignKey(Drink, blank=True, null=True,
      related_name='events',
      help_text='Drink involved in the event, if any.')
  keg = models.ForeignKey(Keg, blank=True, null=True,
      related_name='events',
      help_text='Keg involved in the event, if any.')
  session = models.ForeignKey(DrinkingSession, blank=True, null=True,
      related_name='events',
      help_text='Session involved in the event, if any.')

  def __str__(self):
    if self.kind == 'drink_poured':
      ret = 'Drink %i poured' % self.drink.id
    elif self.kind == 'session_started':
      ret = 'Session %s started by drink %s' % (self.session.id,
          self.drink.id)
    elif self.kind == 'session_joined':
      ret = 'Session %s joined by %s (drink %s)' % (self.session.id,
          self.user.username, self.drink.id)
    elif self.kind == 'keg_tapped':
      ret = 'Keg %s tapped' % self.keg.id
    elif self.kind == 'keg_ended':
      ret = 'Keg %s ended' % self.keg.id
    else:
      ret = 'Unknown event type (%s)' % self.kind
    return 'Event %s: %s' % (self.id, ret)

  @classmethod
  def ProcessKeg(cls, keg):
    site = keg.site
    if keg.status == 'online':
      q = keg.events.filter(kind='keg_tapped')
      if q.count() == 0:
        e = keg.events.create(site=site, kind='keg_tapped', time=keg.start_time,
            keg=keg)
        e.save()

    if keg.status == 'offline':
      q = keg.events.filter(kind='keg_ended')
      if q.count() == 0:
        e = keg.events.create(site=site, kind='keg_ended', time=keg.end_time,
            keg=keg)
        e.save()

  @classmethod
  def ProcessDrink(cls, drink):
    keg = drink.keg
    session = drink.session
    site = drink.site
    user = drink.user

    if keg:
      q = keg.events.filter(kind='keg_tapped')
      if q.count() == 0:
        e = keg.events.create(site=site, kind='keg_tapped', time=drink.time,
            keg=keg, user=user, drink=drink, session=session)
        e.save()

    if session:
      q = session.events.filter(kind='session_started')
      if q.count() == 0:
        e = session.events.create(site=site, kind='session_started',
            time=session.start_time, drink=drink, user=user)
        e.save()

    if user:
      q = user.events.filter(kind='session_joined', session=session)
      if q.count() == 0:
        e = user.events.create(site=site, kind='session_joined',
            time=drink.time, session=session, drink=drink, user=user)
        e.save()

    q = drink.events.filter(kind='drink_poured')
    if q.count() == 0:
      e = drink.events.create(site=site, kind='drink_poured',
          time=drink.time, drink=drink, user=user, keg=keg,
          session=session)
      e.save()


def _pics_file_name(instance, filename):
  rand_salt = random.randrange(0xffff)
  new_filename = '%04x-%s' % (rand_salt, filename)
  return os.path.join('pics', new_filename)

class Picture(models.Model):
  site = models.ForeignKey(KegbotSite, related_name='pictures',
      blank=True, null=True,
      help_text='Site owning this picture')
  image = models.ImageField(upload_to=_pics_file_name,
      help_text='The image')
  resized = imagespecs.resized
  thumbnail = imagespecs.thumbnail
  small_resized = imagespecs.small_resized
  small_thumbnail = imagespecs.small_thumbnail

  time = models.DateTimeField(default=timezone.now)

  def __str__(self):
    return 'Picture: %s' % self.image


class PourPicture(models.Model):
  '''Stores additional metadata about a picture taken during a pour.'''
  picture = models.ForeignKey('Picture')
  drink = models.ForeignKey(Drink, blank=True, null=True,
      related_name='pictures',
      help_text='Drink this picture is associated with, if any')
  time = models.DateTimeField(default=timezone.now)
  caption = models.TextField(blank=True, null=True,
      help_text='Caption for the picture')
  user = models.ForeignKey(User, blank=True, null=True,
      help_text='User this picture is associated with, if any')
  keg = models.ForeignKey(Keg, blank=True, null=True, related_name='pictures',
      help_text='Keg this picture is associated with, if any')
  session = models.ForeignKey(DrinkingSession, blank=True, null=True,
      on_delete=models.SET_NULL,
      related_name='pictures',
      help_text='Session this picture is associated with, if any')

  def GetCaption(self):
    if self.caption:
      return self.caption
    elif self.drink:
      if self.user:
        return '%s pouring drink %s' % (self.user.username, self.drink.id)
      else:
        return 'An unknown drinker pouring drink %s' % (self.drink.id,)
    else:
      return ''

